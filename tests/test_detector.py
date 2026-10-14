"""Synthetische Zeitreihen prüfen Kausalität, Ausfall und Quellenwechsel."""
import asyncio
import unittest
from collections import Counter
from dataclasses import replace
from datetime import timedelta
from math import ceil
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from test_foundation import T0, event, parameters

from custom_components.ha_sauna.bindings import Bindings
from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.curve_change import (
    CurveFeatures,
    CurveLevelFeatures,
    CurveTrendFeatures,
)
from custom_components.ha_sauna.core.detector import Detector
from custom_components.ha_sauna.core.models import Measurement, Position, Quantity
from custom_components.ha_sauna.core.moisture import (
    WATER_VAPOR_GAS_CONSTANT,
    absolute_humidity,
    saturation_vapor_pressure,
)
from custom_components.ha_sauna.core.parameters import BY_KEY, EDITABLE_DEFINITIONS, Parameters
from custom_components.ha_sauna.core.timeline import Confirmation, Event, Kind
from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime


def detection_parameters(**values):
    baseline = parameters().as_dict()
    baseline["standard_temperature_c"] = max(
        baseline["standard_temperature_c"],
        values.get("sauna_min_temperature_c", baseline["sauna_min_temperature_c"]),
    )
    return Parameters({**baseline, **values})


def measurement(pos, quantity, value, second, source=None, measured=None):
    return Measurement(pos, quantity, value, str(value), source or f"sensor.{pos}_{quantity}",
        T0 + timedelta(seconds=second), measured)


def sample(detector, second, temperature, humidity, positions=(Position.UPPER, Position.LOWER)):
    for pos in positions:
        detector.accept(measurement(pos, Quantity.TEMPERATURE, temperature, second))
        detector.accept(measurement(pos, Quantity.HUMIDITY, humidity, second))
    return detector.advance(T0 + timedelta(seconds=second), enabled=True)


def trace(second):
    if second < 70:
        return 70, 40
    if second < 85:
        return 70 - .3 * (second - 70), 40 - .08 * (second - 70)
    if second < 150:
        return 65.5, 38.8
    if second < 260:
        return 65.5 + .025 * (second - 150), 38.8 + .02 * (second - 150) + (3 if second >= 240 else 0)
    if second < 280:
        return 68.25, 44
    return 68.25 - .05 * (second - 280), 44 - .06 * (second - 280)


class DetectorTests(unittest.TestCase):
    @staticmethod
    def _door_failure_message(events, traces, **context):
        """Keep complete numbers from the actual event ticks, relative to T0."""
        def relative(value):
            if value is None:
                return None
            return (value - T0).total_seconds()

        def owner(value):
            if isinstance(value, tuple):
                return tuple(owner(item) for item in value)
            return relative(value) if hasattr(value, "tzinfo") else value

        rows = []
        for row in traces:
            if not any(kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE) for kind in row["signals"]):
                continue
            candidates = []
            proposals = []
            for route in ("door_open", "door_open_heating", "door_close"):
                for position, quantities in row["curve_candidates"].get(route, {}).items():
                    for quantity, evidence in quantities.items():
                        candidates.append((
                            route, position, quantity, evidence.get("status"),
                            *(relative(evidence.get(key)) for key in (
                                "onset_at", "first_observed_at", "newest_at",
                                "first_newest_at", "fresh_newest_at",
                            )),
                            *(evidence.get(key) for key in (
                                "slope_before", "slope_after", "slope_change",
                                "noise_scale", "score_gain",
                            )),
                        ))
                for proposal in row["curve_proposals"].get(route, ()):
                    proposals.append((
                        route, proposal["status"], owner(proposal.get("owner_context")),
                        tuple(relative(stamp) for stamp in proposal.get("onsets", ())),
                        relative(proposal.get("confirmed_at")), proposal.get("reason"),
                    ))
            rows.append({
                "at_s": relative(row["at"]),
                "signals": tuple(kind.value for kind in row["signals"]),
                "door_metrics": {
                    position: {key: value for key, value in metrics.items()
                               if key.startswith("door_")}
                    for position, metrics in row["metrics"].items()
                },
                "candidates": candidates, "proposals": proposals,
            })
        event_rows = [
            (item.kind.value, relative(item.effective_at), relative(item.trace_at),
             relative(item.detected_at)) for item in events
        ]
        return (
            f"context={context!r}\nevents(kind,effective_s,trace_s,detected_s)={event_rows!r}\n"
            "candidates(route,position,quantity,status,onset_s,first_observed_s,newest_s,"
            "first_newest_s,fresh_newest_s,slope_before,slope_after,slope_change,noise,gain)\n"
            "proposals(route,status,owner_context_s,onsets_s,confirmed_s,reason)\n"
            + "\n".join(repr(row) for row in rows)
        )

    def test_complete_additive_ventilation_lookback_ends_confirmed_gang(self):
        defaults = parameters().values
        p = detection_parameters(door_humidity_seconds=defaults["strong_window_seconds"],
                                 vent_baseline_seconds=defaults["weak_window_seconds"])
        d = Detector(p, T0)
        c = Controller(p)
        c.set_temperature(80, T0)
        c.begin_session("s", T0)
        c.process(event("entry-open", Kind.DOOR_OPEN, 0))
        c.process(event("close", Kind.DOOR_CLOSE, 0))
        c.process(event("water", Kind.INFUSION, 0))
        d.report_heating(True, T0)
        found = []

        def received(detection):
            found.append(detection)
            c.process(Event(f"detected:{detection.kind}:{detection.trace_at}", "s",
                            detection.kind, detection.effective_at,
                            detection.detected_at, detection.trace_at))

        for second in range(320):
            now = T0 + timedelta(seconds=second)
            humidity = 40 if second <= 40 else None if second <= 180 else 20
            temperature = 80 - min(10, max(0, second - 200) * .2)
            c.set_temperature(temperature, now)
            for position in d.positions:
                d.accept(measurement(position, Quantity.TEMPERATURE, temperature, second))
                d.accept(measurement(position, Quantity.HUMIDITY, humidity, second))
            d.advance(now, enabled=True, allowed=c.recognition_allowed,
                      on_detection=received, recognition_context=c.recognition_context_at)

        self.assertEqual(d.capacity, p.values["door_humidity_seconds"] + p.values["vent_baseline_seconds"] + 1)
        ventilation = [e for e in found if e.kind == Kind.VENTILATION]
        self.assertEqual(len(ventilation), 1)
        self.assertGreaterEqual(ventilation[0].trace_at, T0 + timedelta(seconds=200))
        self.assertNotIn(Kind.DOOR_CLOSE, [e.kind for e in found])
        self.assertIsNone(c.session.timeline.active)
        self.assertEqual(c.session.timeline.gang_count, 1)
        self.assertFalse(c.regulation_inputs.gang_heat_demand)
        self.assertIn("gang_ended", [e.kind for e in c.consumer_events])

    def test_same_route_features_are_calculated_once_per_sample(self):
        d = Detector(detection_parameters(), T0)
        calls = Counter()
        slope, absolute = d._slope, d._absolute_humidity_difference

        def measured(method, name, args):
            calls[d.index, name, args] += 1
            return method(*args)

        d._slope = lambda *args: measured(slope, "slope", args)
        d._absolute_humidity_difference = lambda *args: measured(absolute, "absolute", args)
        found = []
        for second in range(401):
            found += sample(d, second, *trace(second))
        self.assertIn(Kind.DOOR_CLOSE, [e.kind for e in found])
        self.assertIn(Kind.PERSON_STRONG, [e.kind for e in found])
        self.assertTrue(calls)
        self.assertEqual(max(calls.values()), 1)

    def test_opening_loss_can_follow_short_humidity_signal_during_continuous_cooling(self):
        traces = []
        detector = Detector(detection_parameters(), T0, observer=traces.append)
        events = []
        for second in range(280):
            fall = max(0, second - 70)
            events += sample(detector, second, 80 - .04 * fall,
                             30 - min(.6, .12 * fall))
        diagnostic = self._door_failure_message(events, traces, input_turns_s=(70, 75))
        openings = [item for item in events if item.kind == Kind.DOOR_OPEN]
        self.assertEqual(len(openings), 1, msg=diagnostic)
        self.assertNotIn(Kind.DOOR_CLOSE, [item.kind for item in events], msg=diagnostic)
        opening = openings[0]
        # Resolve the declared input turn within the existing bounded knot search.
        resolution = max(detector.grid_seconds, ceil(
            detector.p["curve_window_seconds"]
            / max(1, detector.curve_policy.max_candidates - 1)
        ))
        self.assertLessEqual(
            abs((opening.effective_at - T0).total_seconds() - 70), resolution,
            msg=diagnostic,
        )
        self.assertLess(opening.effective_at, opening.detected_at, msg=diagnostic)
        self.assertEqual(opening.trace_at, opening.detected_at, msg=diagnostic)
        consumed = [
            proposal for row in traces if row["at"] == opening.trace_at
            for proposal in row["curve_proposals"].get("door_open", ())
            if proposal["status"] == "consumed"
        ]
        self.assertEqual(len(consumed), 1, msg=diagnostic)
        proposal = consumed[0]
        self.assertEqual(proposal["consumed_onset_at"], opening.effective_at, msg=diagnostic)
        self.assertLess(proposal["first_observed_at"], opening.detected_at, msg=diagnostic)
        self.assertEqual(proposal["confirmed_at"], opening.detected_at, msg=diagnostic)
        self.assertEqual(
            {(item["position"], item["quantity"]) for item in proposal["witnesses"]},
            {(position.value, quantity.value) for position in detector.positions
             for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY)},
            msg=diagnostic,
        )
        confirmations = {item["candidate_id"]: item for item in proposal["confirmation"]}
        original_times = {T0 + timedelta(seconds=second) for second in range(280)}
        for witness in proposal["witnesses"]:
            confirmation = confirmations[witness["candidate_id"]]
            self.assertIn(witness["onset_at"], original_times, msg=diagnostic)
            self.assertLessEqual(witness["newest_at"], proposal["first_observed_at"],
                                 msg=diagnostic)
            self.assertTrue(confirmation["supported"], msg=diagnostic)
            self.assertIn(confirmation["fresh_newest_at"], original_times, msg=diagnostic)
            self.assertGreater(confirmation["fresh_newest_at"], witness["first_newest_at"],
                               msg=diagnostic)
            self.assertLessEqual(confirmation["newest_at"], opening.trace_at, msg=diagnostic)

    def test_small_coherent_door_shapes_do_not_repeat_during_plateau(self):
        for thermal in (False, True):
            for positions in ((Position.UPPER,), (Position.UPPER, Position.LOWER)):
                with self.subTest(thermal=thermal, positions=positions):
                    traces = []
                    detector = Detector(detection_parameters(), T0, positions,
                                        observer=traces.append)
                    events = []
                    for second in range(130):
                        detector.report_heating(thermal, T0 + timedelta(seconds=second))
                        # Both routes receive an exact small form with turns at
                        # 30/50/70; its amplitude does not make it mere noise.
                        fall = max(0, min(second - 30, 70 - second, 20))
                        events += sample(
                            detector, second, 80 - .04 * fall,
                            30 if thermal else 30 - .08 * fall, positions,
                        )
                    diagnostic = self._door_failure_message(
                        events, traces, thermal=thermal, positions=positions,
                        input_turns_s=(30, 50, 70),
                    )
                    doors = [item for item in events
                             if item.kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE)]
                    self.assertEqual([item.kind for item in doors],
                                     [Kind.DOOR_OPEN, Kind.DOOR_CLOSE], msg=diagnostic)
                    resolution = max(detector.grid_seconds, ceil(
                        detector.p["curve_window_seconds"]
                        / max(1, detector.curve_policy.max_candidates - 1)
                    ))
                    for item, onset in zip(doors, (30, 50), strict=True):
                        self.assertLessEqual(
                            abs((item.effective_at - T0).total_seconds() - onset),
                            resolution, msg=diagnostic,
                        )
                    # No fresh shape starts in the constant tail from 70 to 130.
                    self.assertFalse(any(
                        T0 + timedelta(seconds=70) <= item.effective_at
                        < T0 + timedelta(seconds=130) for item in doors
                    ), msg=diagnostic)
                    # A separate loss begins at 130; its plateau begins at 150.
                    for second in range(130, 180):
                        detector.report_heating(thermal, T0 + timedelta(seconds=second))
                        fall = min(20, second - 130)
                        events += sample(detector, second, 80 - .15 * fall,
                                         30 if thermal else 30 - .1 * fall, positions)
                    diagnostic = self._door_failure_message(
                        events, traces, thermal=thermal, positions=positions,
                        input_turns_s=(30, 50, 70, 130, 150),
                    )
                    doors = [item for item in events
                             if item.kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE)]
                    self.assertIn([item.kind for item in doors], (
                        [Kind.DOOR_OPEN, Kind.DOOR_CLOSE, Kind.DOOR_OPEN],
                        [Kind.DOOR_OPEN, Kind.DOOR_CLOSE, Kind.DOOR_OPEN, Kind.DOOR_CLOSE],
                    ), msg=diagnostic)
                    for item, onset in zip(doors[2:], (130, 150)):
                        self.assertLessEqual(
                            abs((item.effective_at - T0).total_seconds() - onset),
                            resolution, msg=diagnostic,
                        )

    def test_opening_shape_requires_original_temperature_evidence_at_both_positions(self):
        for lower_falls in (True, False):
            with self.subTest(lower_falls=lower_falls):
                traces = []
                detector = Detector(detection_parameters(), T0, observer=traces.append)
                events = []
                lower_rate = .03 if lower_falls else 0
                for second in range(90):
                    detector.report_heating(True, T0 + timedelta(seconds=second))
                    fall = min(20, max(0, second - 30))
                    for position, rate in ((Position.UPPER, .12), (Position.LOWER, lower_rate)):
                        detector.accept(measurement(position, Quantity.TEMPERATURE,
                                                    80 - rate * fall, second))
                        detector.accept(measurement(position, Quantity.HUMIDITY,
                                                    30 - .08 * fall, second))
                    events += detector.advance(T0 + timedelta(seconds=second), enabled=True)
                diagnostic = self._door_failure_message(
                    events, traces, input_turns_s=(30, 50), temperature_rates=(.12, lower_rate),
                )
                openings = [item for item in events if item.kind == Kind.DOOR_OPEN]
                if not lower_falls:
                    # RH still falls at both positions, but the lower T has no turn.
                    self.assertEqual(openings, [], msg=diagnostic)
                    continue
                self.assertEqual(len(openings), 1, msg=diagnostic)
                opening = openings[0]
                self.assertEqual(opening.channels, ("upper", "lower"), msg=diagnostic)
                resolution = max(detector.grid_seconds, ceil(
                    detector.p["curve_window_seconds"]
                    / max(1, detector.curve_policy.max_candidates - 1)
                ))
                self.assertLessEqual(
                    abs((opening.effective_at - T0).total_seconds() - 30), resolution,
                    msg=diagnostic,
                )
                consumed = [
                    proposal for row in traces if row["at"] == opening.trace_at
                    for route in ("door_open", "door_open_heating")
                    for proposal in row["curve_proposals"].get(route, ())
                    if proposal["status"] == "consumed"
                ]
                self.assertEqual(len(consumed), 1, msg=diagnostic)
                proposal = consumed[0]
                self.assertEqual(proposal["positions"], ("upper", "lower"), msg=diagnostic)
                self.assertEqual(proposal["consumed_onset_at"], opening.effective_at, msg=diagnostic)
                self.assertLess(proposal["first_observed_at"], opening.detected_at, msg=diagnostic)
                self.assertEqual(proposal["confirmed_at"], opening.detected_at, msg=diagnostic)
                temperature_witnesses = [item for item in proposal["witnesses"]
                                         if item["quantity"] == Quantity.TEMPERATURE.value]
                self.assertEqual({item["position"] for item in temperature_witnesses},
                                 {"upper", "lower"}, msg=diagnostic)
                confirmations = {item["candidate_id"]: item for item in proposal["confirmation"]}
                original_times = {T0 + timedelta(seconds=second) for second in range(90)}
                for witness in temperature_witnesses:
                    confirmation = confirmations[witness["candidate_id"]]
                    self.assertEqual(witness["source"], f"sensor.{witness['position']}_temperature",
                                     msg=diagnostic)
                    self.assertIn(witness["onset_at"], original_times, msg=diagnostic)
                    self.assertTrue(confirmation["supported"], msg=diagnostic)
                    self.assertIn(confirmation["fresh_newest_at"], original_times, msg=diagnostic)
                    self.assertGreater(confirmation["fresh_newest_at"], witness["first_newest_at"],
                                       msg=diagnostic)
                    self.assertLessEqual(confirmation["newest_at"], opening.trace_at, msg=diagnostic)

    def test_finite_temperature_outlier_keeps_archived_cycle_and_output_alive(self):
        async def exercise(path):
            now = [T0]
            runtime = SaunaRuntime(
                Configuration(Bindings({
                    "upper_temperature": "sensor.top_t",
                    "upper_humidity": "sensor.top_h",
                    "lower_temperature": "sensor.bottom_t",
                    "lower_humidity": "sensor.bottom_h",
                    "heater": "switch.heater",
                    "light": "light.sauna",
                    "control_input": "event.button",
                }), parameters()),
                lambda: now[0],
            )

            class Device:
                button_event_types = None
                measurements = {}
                command = False
                command_error = False
                faults = {}

                def feedback(self):
                    return False

                def refresh(self, at):
                    runtime.controller.advance(at)

                async def apply(self, at):
                    outputs.append(at)

            outputs = []
            runtime.device = Device()
            await runtime.start_archive(path, "outlier-entry")
            runtime.controller.begin_session("outlier", T0)
            runtime._sync_detector()
            for second in range(18):
                now[0] = T0 + timedelta(seconds=second)
                for position in (Position.UPPER, Position.LOWER):
                    temperature = 1e308 if position == Position.UPPER and 12 <= second <= 16 else 80.0
                    for quantity, value in (
                        (Quantity.TEMPERATURE, temperature),
                        (Quantity.HUMIDITY, 40.0),
                    ):
                        m = measurement(position, quantity, value, second)
                        runtime.detector.accept(m)
                        runtime.archive.append("measurement", now[0], m, "outlier")
                await runtime._cycle()
            await runtime.archive.flush()
            records = runtime.archive.read("outlier")["records"]
            await runtime.archive.close()
            return outputs, records

        with TemporaryDirectory() as directory:
            outputs, records = asyncio.run(exercise(Path(directory) / "outlier.sqlite"))
        self.assertEqual(len(outputs), 18)
        self.assertEqual(sum(r["kind"] == "measurement" for r in records), 72)
        self.assertTrue(any(r["kind"] == "detector_trace" for r in records))

    def test_temperature_veto_legacy_values_are_not_editable(self):
        editable = {definition.key for definition in EDITABLE_DEFINITIONS}
        self.assertTrue({
            "strong_temperature_upper", "strong_temperature_lower",
            "weak_temperature_upper", "weak_temperature_lower",
            "infusion_temperature",
        }.isdisjoint(editable))

    def _ventilation_detector(self, *, positions=(Position.UPPER, Position.LOWER)):
        return Detector(detection_parameters(), T0, positions)


    @staticmethod
    def _humidity_for_water(temperature, water):
        return water * WATER_VAPOR_GAS_CONSTANT * (temperature + 273.15) * 100 / (
            saturation_vapor_pressure(temperature) * 1000)

    def _segment(self, detector, duration, before, after=None, *, positions=None,
                 missing=(), heating=False, source=None):
        """Continuous linear measurement inputs; detector windows stay configured."""
        after = before if after is None else after
        positions = detector.positions if positions is None else positions
        output = []
        for index in range(ceil(duration)):
            second = detector.index + 1
            fraction = (index + 1) / ceil(duration)
            now = T0 + timedelta(seconds=second)
            detector.report_heating(heating, now)
            for position in positions:
                for quantity, initial, final in zip(
                    (Quantity.TEMPERATURE, Quantity.HUMIDITY), before, after, strict=True
                ):
                    value = (None if (position, quantity) in missing or initial is None or final is None
                             else initial + (final - initial) * fraction)
                    detector.accept(measurement(position, quantity, value, second, source=source))
            output += detector.advance(now, enabled=True)
        return output

    @staticmethod
    def _settling_time(detector):
        return ceil(max(detector.p["weak_window_seconds"], detector.p["strong_window_seconds"],
                        detector.p["vent_baseline_seconds"]) + detector.p["median_seconds"]
                    + max(detector.p["door_window_seconds"], detector.p["door_humidity_seconds"]))

    def _open_for_ventilation(self, detector, *, positions=None, heating=False):
        """Short opening: a measured 2 °C loss, insufficient for ventilation."""
        positions = detector.positions if positions is None else positions
        events = self._segment(detector, self._settling_time(detector), (80, 40),
                               positions=positions, heating=heating)
        events += self._segment(detector, 20, (80, 40), (78, 38),
                                positions=positions, heating=heating)
        events += self._segment(detector, detector.p["median_seconds"], (78, 38),
                                positions=positions, heating=heating)
        self.assertIn(Kind.DOOR_OPEN, [item.kind for item in events])
        self.assertNotIn(Kind.VENTILATION, [item.kind for item in events])
        return events


    def _entry_detector(self):
        return Detector(detection_parameters(), T0)


    def _entry_episode(self, detector, *, moisture=True):
        events = self._opened_entry_episode(detector)
        events += self._segment(detector, 30, (78, 38), (80, 38))
        if moisture:
            duration = (detector.p["weak_window_seconds"] + detector.p["median_seconds"]
                        + detector.p["weak_hold_seconds"] + 2 * detector.p["person_step_seconds"])
            events += self._segment(detector, duration, (80, 38),
                                    (80, 38 + duration * .02))
        return events


    def _opened_entry_episode(self, detector):
        return self._open_for_ventilation(detector)


    def test_brief_nonventing_open_close_enables_weak_person_signal(self):
        detector = self._entry_detector()
        events = self._entry_episode(detector)
        self.assertEqual([item.kind for item in events if item.kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE)],
                         [Kind.DOOR_OPEN, Kind.DOOR_CLOSE])
        self.assertNotIn(Kind.VENTILATION, [item.kind for item in events])
        weak = next(item for item in events if item.kind is Kind.PERSON_WEAK)
        close = next(item for item in events if item.kind is Kind.DOOR_CLOSE)
        self.assertGreater(weak.effective_at, close.effective_at)


    def test_cold_entry_keeps_weak_detection_available_after_warming(self):
        traces = []
        detector = Detector(detection_parameters(), T0, observer=traces.append)
        c = Controller(Parameters(detector.p))
        minimum = c.parameters.values["sauna_min_temperature_c"]
        c.set_temperature(minimum - 1, T0)
        c.begin_session("s", T0)
        baseline = self._settling_time(detector)
        close_at = baseline + 20 + 30
        warm_at = close_at + ceil(c.parameters.seconds("confirmation_minutes")) + 1
        duration = ceil(detector.p["weak_window_seconds"] + detector.p["median_seconds"]
                        + detector.p["weak_hold_seconds"] + 2 * detector.p["person_step_seconds"])
        observed = []

        def received(detection):
            observed.append(detection)
            c.process(Event(f"detected:{detection.kind}:{detection.trace_at}", "s",
                            detection.kind, detection.effective_at, detection.detected_at,
                            detection.trace_at), recognition_at=detection.trace_at)

        for second in range(warm_at + duration):
            now = T0 + timedelta(seconds=second)
            if second < baseline:
                temperature, humidity = minimum - 1, 40
            elif second < baseline + 20:
                fraction = (second - baseline + 1) / 20
                temperature, humidity = minimum - 1 - 2 * fraction, 40 - 2 * fraction
            elif second < close_at:
                fraction = (second - baseline - 19) / 30
                temperature, humidity = minimum - 3 + 2 * fraction, 38
            else:
                temperature = minimum if second >= warm_at else minimum - 1
                humidity = 38 + max(0, second - warm_at) * .02
            c.set_temperature(temperature, now)
            for position in detector.positions:
                detector.accept(measurement(position, Quantity.TEMPERATURE, temperature, second))
                detector.accept(measurement(position, Quantity.HUMIDITY, humidity, second))
            detector.advance(now, enabled=True, allowed=c.recognition_allowed,
                             allowed_at=c.recognition_allowed_at,
                             recognition_context=c.recognition_context_at, on_detection=received)
            if second < warm_at:
                self.assertIsNone(c.session.timeline.active)
        diagnostic = self._door_failure_message(
            observed, traces, baseline_s=baseline, close_at_s=close_at,
            warm_at_s=warm_at, minimum=minimum,
        )
        self.assertEqual([item.kind for item in observed if item.kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE)],
                         [Kind.DOOR_OPEN, Kind.DOOR_CLOSE], msg=diagnostic)
        warm_time = T0 + timedelta(seconds=warm_at)
        self.assertTrue(any(
            item.kind in (Kind.PERSON_STRONG, Kind.PERSON_WEAK) and item.trace_at >= warm_time
            for item in observed
        ), msg=diagnostic)
        # The continuing RH rise has neither a peak nor a falling original tail.
        self.assertNotIn(Kind.INFUSION, [item.kind for item in observed], msg=diagnostic)
        self.assertIsNotNone(c.session.timeline.active, msg=diagnostic)
        self.assertEqual(c.session.timeline.active.started_at, warm_time,
                         msg=diagnostic)
        self.assertEqual(c.session.timeline.active.confirmation, Confirmation.PROVISIONAL,
                         msg=diagnostic)


    def test_close_recovers_when_lower_humidity_drops_out(self):
        detector = self._entry_detector()
        events = self._opened_entry_episode(detector)
        missing = ((Position.LOWER, Quantity.HUMIDITY),)
        events += self._segment(detector, 30, (78, 38), (80, 38), missing=missing)
        close = next(item for item in events if item.kind is Kind.DOOR_CLOSE)
        self.assertEqual(close.channels, ("upper", "lower"))


    def test_close_recovers_without_humidity_pairs(self):
        detector = self._entry_detector()
        events = self._opened_entry_episode(detector)
        missing = tuple((position, Quantity.HUMIDITY) for position in detector.positions)
        events += self._segment(detector, 30, (78, 38), (80, 38), missing=missing)
        close = next(item for item in events if item.kind is Kind.DOOR_CLOSE)
        self.assertEqual(close.channels, ("upper", "lower"))


    def test_close_can_recover_with_one_remaining_temperature_role(self):
        detector = self._entry_detector()
        events = self._opened_entry_episode(detector)
        missing = ((Position.LOWER, Quantity.TEMPERATURE), (Position.LOWER, Quantity.HUMIDITY))
        events += self._segment(detector, 30, (78, 38), (80, 38), missing=missing)
        close = next(item for item in events if item.kind is Kind.DOOR_CLOSE)
        self.assertEqual(close.channels, ("upper",))


    def test_missing_temperatures_reset_the_close_hold(self):
        detector = self._entry_detector()
        self._opened_entry_episode(detector)
        events = []
        # The first recovering samples precede a missing-temperature interval.
        for recovery_step in range(ceil(detector.p["door_window_seconds"] + detector.p["median_seconds"] + detector.p["door_close_hold_seconds"]) + 1):
            step = detector.index + 1
            temperature = 78 + (recovery_step + 1) * .1
            events += sample(detector, step, temperature, 38)
            self.assertTrue(detector.open)
            if detector.diagnostic["conditions"].get("door_close"):
                break
        self.assertTrue(detector.diagnostic["conditions"].get("door_close"))
        gap_at = detector.index + 1
        for position in detector.positions:
            detector.accept(measurement(position, Quantity.TEMPERATURE, None, gap_at))
        events += detector.advance(T0 + timedelta(seconds=gap_at), enabled=True)
        self.assertEqual(detector.diagnostic["holds"].get("door", 0), 0)
        events += sample(detector, gap_at + 1, temperature + .01, 38)
        self.assertTrue(detector.open)
        self.assertNotIn(Kind.DOOR_CLOSE, [item.kind for item in events])


    def test_preopening_recovery_hint_cannot_close_a_later_falling_opening(self):
        detector = self._entry_detector()
        events = self._segment(detector, self._settling_time(detector), (78, 40), (80, 40))
        events += self._segment(detector, 30, (80, 40), (76, 36))
        self.assertIn(Kind.DOOR_OPEN, [item.kind for item in events])
        self.assertNotIn(Kind.DOOR_CLOSE, [item.kind for item in events])


    def test_weak_needs_a_recent_actual_close_and_new_opening_resets_its_hold(self):
        detector = self._entry_detector()
        duration = self._settling_time(detector) + detector.p["weak_hold_seconds"]
        events = self._segment(detector, duration, (80, 30), (80, 30 + .02 * duration))
        self.assertNotIn(Kind.PERSON_WEAK, [item.kind for item in events])
        events = self._entry_episode(detector, moisture=False)
        expiry = ceil(detector.p["confirmation_minutes"] * 60) + 1
        events += self._segment(detector, expiry, (80, 38))
        events += self._segment(detector, duration, (80, 38), (80, 38 + .02 * duration))
        self.assertNotIn(Kind.PERSON_WEAK, [item.kind for item in events])
        self.assertFalse(detector.diagnostic["checks"].get("weak", False))
        self.assertEqual(detector.diagnostic["holds"].get("weak", 0), 0)
        # A new opening retires the old entry and its unfinished weak proof.
        events += self._segment(detector, 30, (80, 38), (76, 34))
        self.assertTrue(detector.open)
        self.assertEqual(detector.diagnostic["holds"].get("weak", 0), 0)


    def test_hot_stable_temperature_with_actual_water_gain_confirms_person(self):
        detector = Detector(detection_parameters(), T0)
        duration = self._settling_time(detector) + detector.p["strong_hold_seconds"]
        events = self._segment(detector, duration, (85, 30), (85, 30 + .03 * duration))
        self.assertIn(Kind.PERSON_STRONG, [item.kind for item in events])


    def test_cooling_can_raise_relative_humidity_without_person_or_infusion(self):
        detector = Detector(detection_parameters(), T0)
        water = absolute_humidity(80, 30)
        events = []
        duration = self._settling_time(detector) + detector.p["strong_hold_seconds"]
        for second in range(ceil(duration)):
            temperature = 80 - 5 * second / duration
            events += sample(detector, second, temperature, self._humidity_for_water(temperature, water))
        kinds = [item.kind for item in events]
        self.assertNotIn(Kind.PERSON_STRONG, kinds)
        self.assertNotIn(Kind.INFUSION, kinds)


    def test_infusion_with_fan_cooling_needs_water_gain_not_temperature_rise(self):
        detector = Detector(detection_parameters(), T0)
        events = self._segment(detector, self._settling_time(detector), (80, 30))
        events += self._segment(detector, 25, (80, 30), (78, 40))
        self.assertIn(Kind.INFUSION, [item.kind for item in events])


    def test_preopening_moisture_rise_cannot_become_weak_person_after_close(self):
        detector = self._entry_detector()
        duration = self._settling_time(detector)
        events = self._segment(detector, duration, (80, 30))
        events += self._segment(detector, duration, (80, 30), (80, 30 + .02 * duration), heating=True)
        humidity = 30 + .02 * duration
        # Added water continues throughout this opening; the thermal drop must
        # not turn the earlier moisture rise into a new false→true evidence edge.
        events += self._segment(detector, 30, (80, humidity), (76, humidity + 10), heating=True)
        events += self._segment(detector, 30, (76, humidity + 10), (80, humidity + 10.6), heating=True)
        events += self._segment(detector, duration, (80, humidity + 10.6),
                                (80, humidity + 10.6 + .02 * duration), heating=True)
        self.assertIn(Kind.DOOR_CLOSE, [item.kind for item in events])
        self.assertNotIn(Kind.PERSON_WEAK, [item.kind for item in events])


    def test_source_gap_restarts_strong_hold_after_a_complete_window(self):
        detector = Detector(detection_parameters(), T0)
        p = detector.p
        step = p["person_step_seconds"]
        first_full = ceil((p["median_seconds"] - 1 + p["strong_window_seconds"]) / step) * step
        events = []
        for second in range(ceil(first_full) + 1):
            events += sample(detector, second, 80, 30 + .03 * second)
        self.assertGreater(detector.diagnostic["holds"].get("strong", 0), 0)
        self.assertNotIn(Kind.PERSON_STRONG, [item.kind for item in events])
        gap_at = detector.index + 1
        for position in (Position.UPPER,):
            for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY):
                detector.accept(measurement(position, quantity, None, gap_at))
        events += detector.advance(T0 + timedelta(seconds=gap_at), enabled=True)
        self.assertEqual(detector.diagnostic["holds"].get("strong", 0), 0)
        before_full = gap_at + ceil(p["strong_window_seconds"])
        for second in range(gap_at + 1, before_full + 1):
            events += sample(detector, second, 80, 30 + .03 * second)
        self.assertNotIn(Kind.PERSON_STRONG, [item.kind for item in events])
        complete = before_full + ceil(p["median_seconds"] + p["strong_hold_seconds"] + 2 * step)
        for second in range(before_full + 1, complete + 1):
            events += sample(detector, second, 80, 30 + .03 * second)
        self.assertEqual([item.kind for item in events].count(Kind.PERSON_STRONG), 1)


    def test_gap_restarts_an_unfinished_strong_hold(self):
        detector = Detector(detection_parameters(), T0)
        p = detector.p
        step = p["person_step_seconds"]
        first_full = ceil((p["median_seconds"] - 1 + p["strong_window_seconds"]) / step) * step
        events = []
        for second in range(ceil(first_full) + 1):
            events += sample(detector, second, 80, 30 + .03 * second)
        self.assertGreater(detector.diagnostic["holds"].get("strong", 0), 0)
        self.assertNotIn(Kind.PERSON_STRONG, [item.kind for item in events])
        gap_at = detector.index + 1
        for position in detector.positions:
            for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY):
                detector.accept(measurement(position, quantity, None, gap_at))
        events += detector.advance(T0 + timedelta(seconds=gap_at), enabled=True)
        self.assertEqual(detector.diagnostic["holds"].get("strong", 0), 0)
        before_full = gap_at + ceil(p["strong_window_seconds"])
        for second in range(gap_at + 1, before_full + 1):
            events += sample(detector, second, 80, 30 + .03 * second)
        self.assertNotIn(Kind.PERSON_STRONG, [item.kind for item in events])
        complete = before_full + ceil(p["median_seconds"] + p["strong_hold_seconds"] + 2 * step)
        for second in range(before_full + 1, complete + 1):
            events += sample(detector, second, 80, 30 + .03 * second)
        self.assertEqual([item.kind for item in events].count(Kind.PERSON_STRONG), 1)


    def test_two_sensor_water_loss_confirms_without_the_legacy_open_duration(self):
        observed = []
        detector = Detector(detection_parameters(), T0, observer=observed.append)
        events = self._open_for_ventilation(detector)
        events += self._segment(detector, 20, (78, 38), (74, 20))
        ventilation = next(item for item in events if item.kind is Kind.VENTILATION)
        self.assertLess((ventilation.detected_at - detector.opened_at).total_seconds(), detector.p["vent_hold_seconds"])
        trace = next(row for row in observed if Kind.VENTILATION in row["signals"])
        metric = trace["metrics"]["upper"]
        self.assertGreaterEqual(metric["ventilation_temperature_loss"], detector.p["vent_drop_upper"])
        self.assertGreaterEqual(metric["ventilation_absolute_humidity_loss"], detector.p["vent_absolute_humidity_loss_percent"] / 100)


    def test_gap_pauses_two_position_ventilation_and_same_sources_can_resume_it(self):
        detector = self._ventilation_detector()
        events = self._open_for_ventilation(detector)
        events += self._segment(detector, 20, (78, 38), (74, 20),
                                missing=((Position.LOWER, Quantity.TEMPERATURE), (Position.LOWER, Quantity.HUMIDITY)))
        self.assertNotIn(Kind.VENTILATION, [item.kind for item in events])
        events += self._segment(detector, detector.p["median_seconds"] + 1, (74, 20))
        self.assertEqual([item.kind for item in events].count(Kind.VENTILATION), 1)


    def test_ventilation_reference_precedes_the_first_delayed_feature_window(self):
        detector = self._ventilation_detector()
        self._segment(detector, self._settling_time(detector), (80, 40))
        self._segment(detector, 20, (80, 40), (78, 40))
        # The temperature hint exists before the later humidity loss qualifies the mixed route.
        self._segment(detector, 20, (78, 40), (76, 38))
        self.assertTrue(detector.open)
        for position in detector.positions:
            self.assertGreater(detector.baseline[position]["temperature"], 78)


    def test_recovery_pauses_ventilation_before_the_close_hold_completes(self):
        detector = self._ventilation_detector()
        events = self._open_for_ventilation(detector)
        # Temperature has fallen sufficiently, but constant water content prevents ventilation.
        water = absolute_humidity(78, 38)
        for _ in range(20):
            second = detector.index + 1
            temperature = 78 - .2 * (_ + 1)
            events += sample(detector, second, temperature, self._humidity_for_water(temperature, water))
        self.assertNotIn(Kind.VENTILATION, [item.kind for item in events])
        # A fresh recovering temperature trend must pause ventilation before the close timer is due.
        recovery = []
        for _ in range(ceil(detector.p["door_window_seconds"] + detector.p["median_seconds"] + detector.p["door_close_hold_seconds"]) + 1):
            second = detector.index + 1
            temperature += .15
            recovery += sample(detector, second, temperature, self._humidity_for_water(temperature, water))
            self.assertTrue(detector.open)
            if detector.diagnostic["conditions"].get("door_close"):
                break
        self.assertTrue(detector.diagnostic["conditions"].get("door_close"))
        self.assertNotIn(Kind.DOOR_CLOSE, [item.kind for item in recovery])
        self.assertTrue(detector.diagnostic["conditions"].get("ventilation_recovery"))
        self.assertNotIn(Kind.VENTILATION, [item.kind for item in recovery])


    def test_single_sensor_keeps_the_legacy_open_duration(self):
        detector = self._ventilation_detector(positions=(Position.UPPER,))
        events = self._open_for_ventilation(detector)
        events += self._segment(detector, 20, (78, 38), (74, 20))
        events += self._segment(detector, detector.p["vent_hold_seconds"], (74, 20))
        ventilation = [item for item in events if item.kind is Kind.VENTILATION]
        self.assertEqual(len(ventilation), 1)
        self.assertGreaterEqual((ventilation[0].detected_at - detector.opened_at).total_seconds(), detector.p["vent_hold_seconds"])


    def test_runtime_positions_confirm_one_position_available_when_the_door_opens(self):
        """Die Runtime behält beide Rollen, obwohl unten vor der Öffnung fehlt."""
        detector = Detector(parameters(), T0)
        events = []
        for second in range(360):
            temperature = 80 if second < 120 else 80 - .2 * (second - 120)
            humidity = 20 if second < 120 else 20 - .08 * (second - 120)
            detector.accept(measurement(Position.UPPER, Quantity.TEMPERATURE, temperature, second))
            detector.accept(measurement(Position.UPPER, Quantity.HUMIDITY, humidity, second))
            events += detector.advance(T0 + timedelta(seconds=second), enabled=True)
        ventilation = [event for event in events if event.kind is Kind.VENTILATION]
        self.assertEqual([event.kind for event in events].count(Kind.DOOR_OPEN), 1)
        self.assertEqual(len(ventilation), 1)
        self.assertEqual(ventilation[0].channels, ("upper",))
        self.assertGreaterEqual((ventilation[0].detected_at - detector.opened_at).total_seconds(), detector.p["vent_hold_seconds"])
        self.assertIn("lower_unavailable", detector.faults)

    def test_used_single_position_source_change_cannot_confirm_the_opening(self):
        detector = Detector(parameters(), T0)
        events = []
        for second in range(360):
            temperature = 80 if second < 120 else 80 - .2 * (second - 120)
            humidity = 20 if second < 120 else 20 - .08 * (second - 120)
            source = "sensor.upper" if second < 150 else "sensor.upper_replaced"
            detector.accept(measurement(Position.UPPER, Quantity.TEMPERATURE, temperature, second, source))
            detector.accept(measurement(Position.UPPER, Quantity.HUMIDITY, humidity, second, source))
            events += detector.advance(T0 + timedelta(seconds=second), enabled=True)
        self.assertIn(Kind.DOOR_OPEN, [event.kind for event in events])
        self.assertNotIn(Kind.VENTILATION, [event.kind for event in events])
        self.assertTrue(detector.ventilation_degraded)

    def test_lost_position_during_open_door_cannot_fall_back_to_single_sensor_proof(self):
        detector = self._ventilation_detector()
        events = self._open_for_ventilation(detector)
        events += self._segment(detector, 20, (78, 38), (74, 20), positions=(Position.UPPER,))
        duration = detector.p["sensor_timeout_seconds"] + detector.p["vent_hold_seconds"] + 1
        events += self._segment(detector, duration, (74, 20), positions=(Position.UPPER,))
        self.assertNotIn(Kind.VENTILATION, [item.kind for item in events])
        self.assertIn(Position.LOWER, detector.baseline)


    def test_source_replacement_during_opening_invalidates_its_reference(self):
        detector = self._ventilation_detector()
        events = self._open_for_ventilation(detector)
        events += self._segment(detector, 20, (78, 38), (74, 20), source="sensor.replaced")
        self.assertNotIn(Kind.VENTILATION, [item.kind for item in events])
        self.assertIn(Position.UPPER, detector.ventilation_invalid)


    def test_door_with_delayed_humidity_drop_during_continuous_heating(self):
        detector = Detector(detection_parameters(), T0)
        events = []
        for i in range(80):
            detector.report_heating(True, T0 + timedelta(seconds=i))
            # Synthetische kurze Öffnung: beidseitig 4,8 °C/min, zunächst
            # sogar steigende Feuchte. Die bisherige UND-Regel reicht nicht.
            temperature = 50 if i < 20 else 50-.08*min(20,i-20)+max(0,i-40)*.03
            events += sample(detector, i, temperature, 30+i*.002)
        self.assertEqual([e.kind for e in events], [Kind.DOOR_OPEN, Kind.DOOR_CLOSE])
        self.assertGreaterEqual((events[0].detected_at-T0).total_seconds(), 25)

    def test_fresh_opening_routes_reach_holds_longer_than_the_feature_window(self):
        for route in ("mixed", "thermal"):
            for hold in (parameters().values["door_open_hold_seconds"],
                         max(parameters().values["door_window_seconds"], parameters().values["door_humidity_seconds"]) + 1,
                         BY_KEY["door_open_hold_seconds"].maximum):
                with self.subTest(route=route, hold=hold):
                    detector = Detector(detection_parameters(**{
                        "door_open_hold_seconds": hold,
                        "door_heating_hold_seconds": hold,
                    }), T0, (Position.UPPER,))
                    events = []
                    for second in range(ceil(hold) + 60):
                        detector.report_heating(route == "thermal", T0 + timedelta(seconds=second))
                        fall = max(0, second - 20)
                        events += sample(detector, second, 90 - .04 * fall,
                                         80 - .06 * fall if route == "mixed" else 30,
                                         (Position.UPPER,))
                    openings = [event for event in events if event.kind == Kind.DOOR_OPEN]
                    self.assertEqual(len(openings), 1)
                    self.assertGreaterEqual(
                        (openings[0].detected_at - openings[0].effective_at).total_seconds(),
                        hold,
                    )

    def test_stale_opening_hints_do_not_complete_a_long_hold(self):
        for route in ("mixed", "thermal"):
            with self.subTest(route=route):
                detector = Detector(detection_parameters(
                    door_open_hold_seconds=2 * max(parameters().values["door_window_seconds"], parameters().values["door_humidity_seconds"]),
                    door_heating_hold_seconds=2 * max(parameters().values["door_window_seconds"], parameters().values["door_humidity_seconds"]),
                ), T0, (Position.UPPER,))
                events = []
                for second in range(100):
                    detector.report_heating(route == "thermal", T0 + timedelta(seconds=second))
                    fall = min(8, max(0, second - 20))
                    events += sample(detector, second, 90 - .04 * fall,
                                     80 - .06 * fall if route == "mixed" else 30,
                                     (Position.UPPER,))
                self.assertNotIn(Kind.DOOR_OPEN, [event.kind for event in events])
                self.assertIsNone(detector.door_episode)

    def test_temperature_rule_does_not_treat_heater_off_as_door_opening(self):
        for heating, lower_falls, duration in ((False, True, 20), (None, True, 20), (True, False, 20), (True, True, 2)):
            with self.subTest(heating=heating, lower_falls=lower_falls, duration=duration):
                d = Detector(detection_parameters(), T0)
                events = []
                for i in range(70):
                    d.report_heating(heating if i >= 20 else True, T0+timedelta(seconds=i))
                    delta = -.03*(i-20) if 20 <= i < 20+duration else 0
                    for position in (Position.UPPER, Position.LOWER):
                        temp = 50+delta if position == Position.UPPER or lower_falls else 50
                        d.accept(measurement(position,Quantity.TEMPERATURE,temp,i))
                        d.accept(measurement(position,Quantity.HUMIDITY,30,i))
                    events += d.advance(T0+timedelta(seconds=i),enabled=True)
                self.assertNotIn(Kind.DOOR_OPEN,[e.kind for e in events])

    def test_heater_change_restarts_additional_door_proof(self):
        for positions in ((Position.UPPER,), (Position.UPPER, Position.LOWER)):
            d=Detector(detection_parameters(),T0,positions)
            events=[]
            for i in range(80):
                # Wiederholtes Aus/Ein verhindert den durchgehenden Nachweis.
                d.report_heating(False if i%8==0 else True,T0+timedelta(seconds=i))
                events+=sample(d,i,50-.03*i,30,positions)
            self.assertNotIn(Kind.DOOR_OPEN,[e.kind for e in events])

    def test_catchup_uses_booked_heating_intervals_at_each_sample_time(self):
        controller = Controller(detection_parameters())
        controller.begin_session("heating", T0)
        for second, heating in ((0, True), (2.25, False), (2.75, True), (5, None), (6.5, True)):
            controller.report_heating(heating, T0 + timedelta(seconds=second))
        observed = []
        detector = Detector(
            detection_parameters(), T0,
            observer=lambda trace: observed.append(detector.heating_since),
        )
        detector.advance(
            T0 + timedelta(seconds=8), enabled=True,
            heating_intervals=controller.session.heating.intervals,
            heating_after=T0 + timedelta(seconds=0.5),
        )
        # The short OFF stretch fits between samples 2 and 3. Unknown covers
        # samples 5 and 6; neither those nor sample 0 may use a future ON.
        expected = (None, 0.5, 0.5, 2.75, 2.75, None, None, 6.5, 6.5)
        self.assertEqual(observed, [
            T0 + timedelta(seconds=second) if second is not None else None
            for second in expected
        ])

    def test_catchup_keeps_earlier_heat_when_last_feedback_is_off(self):
        controller = Controller(detection_parameters())
        controller.begin_session("heating", T0)
        controller.report_heating(True, T0)
        controller.report_heating(False, T0 + timedelta(seconds=40))
        detector = Detector(detection_parameters(), T0, (Position.UPPER,))
        for second in range(61):
            detector.accept(measurement(
                Position.UPPER, Quantity.TEMPERATURE, 50 - .06 * second, second
            ))
            detector.accept(measurement(Position.UPPER, Quantity.HUMIDITY, 30, second))
        events = detector.advance(
            T0 + timedelta(seconds=60), enabled=True,
            heating_intervals=controller.session.heating.intervals,
            heating_gates=((T0, True), (T0 + timedelta(seconds=40), False)),
        )
        self.assertIn(Kind.DOOR_OPEN, [event.kind for event in events])
        self.assertIsNone(detector.heating_since)

    def test_logical_off_on_restarts_thermal_proof_during_physical_on(self):
        controller = Controller(detection_parameters())
        controller.begin_session("heating", T0)
        controller.report_heating(True, T0)
        observed = []
        detector = Detector(
            detection_parameters(), T0, (Position.UPPER,),
            observer=lambda trace: observed.append(detector.heating_since),
        )
        detector.advance(
            T0 + timedelta(seconds=30), enabled=True,
            heating_intervals=controller.session.heating.intervals,
            heating_gates=(
                (T0, True),
                (T0 + timedelta(seconds=28), False),
                (T0 + timedelta(seconds=29), True),
            ),
        )
        self.assertIsNone(observed[28])
        self.assertEqual(observed[29], T0 + timedelta(seconds=29))

    def test_door_hold_starts_again_after_required_position_is_missing(self):
        detector = Detector(detection_parameters(), T0, (Position.UPPER,))
        self._segment(detector, self._settling_time(detector), (80, 30), heating=True)
        limit = ceil(detector.p["door_window_seconds"] + detector.p["median_seconds"]
                     + detector.p["door_heating_hold_seconds"] + detector.p["door_open_drop_c"] / .1)
        temperature = 80
        for _ in range(limit):
            second = detector.index + 1
            temperature -= .1
            detector.report_heating(True, T0 + timedelta(seconds=second))
            sample(detector, second, temperature, 30, (Position.UPPER,))
            if detector.diagnostic["holds"].get("door_heating", 0):
                break
        self.assertGreater(detector.diagnostic["holds"].get("door_heating", 0), 0)
        self.assertFalse(detector.open)
        gap_at = detector.index + 1
        detector.accept(measurement(Position.UPPER, Quantity.TEMPERATURE, None, gap_at))
        detector.advance(T0 + timedelta(seconds=gap_at), enabled=True)
        self.assertEqual(detector.diagnostic["holds"].get("door_heating", 0), 0)
        events = self._segment(detector, detector.p["door_heating_hold_seconds"] - 1,
                               (temperature, 30), (temperature - .2, 30), heating=True)
        self.assertNotIn(Kind.DOOR_OPEN, [item.kind for item in events])

    def test_additional_door_rule_uses_all_available_positions(self):
        cases = (
            ("only upper configured", (Position.UPPER,), (Position.UPPER,), (Position.UPPER,), True),
            ("only lower configured", (Position.LOWER,), (Position.LOWER,), (Position.LOWER,), True),
            ("upper survives", (Position.UPPER, Position.LOWER), (Position.UPPER,), (Position.UPPER,), True),
            ("lower survives", (Position.UPPER, Position.LOWER), (Position.LOWER,), (Position.LOWER,), True),
            ("both fall", (Position.UPPER, Position.LOWER), (Position.UPPER, Position.LOWER), (Position.UPPER, Position.LOWER), True),
            ("one falls", (Position.UPPER, Position.LOWER), (Position.UPPER, Position.LOWER), (Position.UPPER,), False),
        )
        for name, configured, reporting, falling, opens in cases:
            with self.subTest(name=name):
                detector = Detector(detection_parameters(), T0, configured)
                events = []
                for second in range(80):
                    detector.report_heating(True, T0 + timedelta(seconds=second))
                    for position in reporting:
                        temperature = 50 - .03 * second if position in falling else 50
                        detector.accept(measurement(position, Quantity.TEMPERATURE, temperature, second))
                        detector.accept(measurement(position, Quantity.HUMIDITY, 30, second))
                    events += detector.advance(T0 + timedelta(seconds=second), enabled=True)
                self.assertEqual(detector.active_positions, reporting)
                self.assertEqual(Kind.DOOR_OPEN in [event.kind for event in events], opens)

    def test_hot_operation_uses_the_continuous_heating_route(self):
        minimum = parameters().values["sauna_min_temperature_c"]
        for base in (parameters().values["standard_temperature_c"], minimum + 5, minimum - 1):
            d=Detector(detection_parameters(),T0)
            events=[]
            for i in range(80):
                d.report_heating(True,T0+timedelta(seconds=i))
                events+=sample(d,i,base-.03*i,30)
            self.assertIn(Kind.DOOR_OPEN, [e.kind for e in events])

    def test_diagnostic_observer_reports_actual_metrics_without_changing_signals(self):
        observed = []
        detector = Detector(detection_parameters(), T0, observer=observed.append)
        reference = Detector(detection_parameters(), T0)
        events = []
        for i in range(401):
            current = sample(detector, i, *trace(i))
            self.assertEqual(current, sample(reference, i, *trace(i)))
            events.extend(current)
        self.assertEqual(len(observed), 401)
        self.assertEqual(observed[0]["at"], T0)
        self.assertEqual(observed[200]["metrics"]["upper"]["strong_temperature_slope"],
                         observed[200]["metrics"]["lower"]["strong_temperature_slope"])
        self.assertEqual([k for row in observed for k in row["signals"]], [e.kind for e in events])
        self.assertIsNotNone(observed[100]["metrics"]["upper"]["door_temperature_slope"])

    def test_detection_parameter_override_is_consumed(self):
        self.assertEqual(Detector(detection_parameters(door_open_slope=-3), T0).p["door_open_slope"], -3)



    def test_future_measurements_do_not_rewrite_the_past(self):
        detector = Detector(detection_parameters(), T0)
        for i in range(20):
            sample(detector, i, 70, 40)
        detector.accept(measurement(Position.UPPER, Quantity.TEMPERATURE, 10, 30))
        detector.advance(T0 + timedelta(seconds=25), enabled=True)
        self.assertEqual(detector.frames[Position.UPPER][-1]["T"], 70)
        detector.advance(T0 + timedelta(seconds=30), enabled=True)
        self.assertEqual(detector.frames[Position.UPPER][-1]["T"], 10)
        self.assertFalse(detector.accept(measurement(Position.UPPER, Quantity.TEMPERATURE, 90, 29)))

    def test_failure_single_sensor_operation_and_rejoin(self):
        detector = Detector(detection_parameters(), T0)
        self._segment(detector, self._settling_time(detector), (80, 40))
        self._segment(detector, detector.p["sensor_timeout_seconds"] + 1, (80, 40),
                      positions=(Position.LOWER,))
        self.assertEqual(detector.active_positions, (Position.LOWER,))
        self.assertIn("upper_unavailable", detector.faults)
        events = self._segment(detector, 20, (80, 40), (78, 38), positions=(Position.LOWER,))
        opening = next(item for item in events if item.kind == Kind.DOOR_OPEN)
        self.assertEqual(opening.channels, ("lower",))
        self._segment(detector, detector.p["median_seconds"], (78, 38))
        self.assertEqual(detector.active_positions, detector.positions)
        self.assertEqual(detector.faults, ())

    def test_source_replacement_discards_old_history_and_hold_evidence(self):
        detector = Detector(detection_parameters(), T0, (Position.UPPER,))
        for i in range(50):
            sample(detector, i, 80, 40, (Position.UPPER,))
        detector.accept(measurement(Position.UPPER, Quantity.TEMPERATURE, 20, 50, source="sensor.replaced"))
        detector.accept(measurement(Position.UPPER, Quantity.HUMIDITY, 10, 50))
        self.assertEqual(detector.advance(T0 + timedelta(seconds=50), enabled=True), [])
        self.assertEqual(len(detector.frames[Position.UPPER]), 1)
        self.assertIsNone(detector.frames[Position.UPPER][-1]["Tm"])

    def test_missing_implausible_or_old_source_timestamp_is_not_healthy(self):
        for value, measured in ((None, None), (101, None), (40, T0 - timedelta(seconds=parameters().values["sensor_timeout_seconds"] + 1))):
            with self.subTest(value=value, measured=measured):
                detector = Detector(detection_parameters(), T0)
                detector.accept(measurement(Position.UPPER, Quantity.TEMPERATURE, 70, 0))
                detector.accept(measurement(Position.UPPER, Quantity.HUMIDITY, value, 0, measured=measured))
                self.assertEqual(detector.advance(T0, enabled=True), [])
                self.assertEqual(detector.active_positions, ())

    def test_report_after_tick_at_equal_timestamp_is_used_next_tick_without_rewriting_history(self):
        detector = Detector(detection_parameters(), T0, (Position.UPPER,))
        sample(detector, 0, 70, 40, (Position.UPPER,))
        self.assertTrue(detector.accept(measurement(Position.UPPER, Quantity.TEMPERATURE, 71, 0)))
        self.assertEqual(detector.frames[Position.UPPER][-1]["T"], 70)
        detector.advance(T0 + timedelta(seconds=1), enabled=True)
        self.assertEqual(detector.frames[Position.UPPER][-1]["T"], 71)
        self.assertEqual(detector.frames[Position.UPPER][-2]["T"], 70)


class InfusionLifecycleTests(unittest.TestCase):
    """Controlled local math; acceptance, AH, event ownership and time stay real."""

    def setUp(self):
        self.parameters = detection_parameters()
        self.p = self.parameters.values
        self.step = int(self.p["grid_seconds"])
        self.side = self.p["curve_min_side_samples"]
        self.width = (2 * self.side + 2) * self.step
        self.begin = (
            ceil(max(
                self.p["infusion_window_seconds"] + self.p["median_seconds"],
                2 * self.side * self.step,
            ) / self.step) + 1
        ) * self.step
        # Reuse the established input fixture's baseline, peak and settled level.
        self.temperature, self.baseline = trace(0)
        self.peak = trace(260)[1]
        self.level = trace(100)[1]

    @staticmethod
    def _at(second):
        return T0 + timedelta(seconds=second)

    @staticmethod
    def _value(points, second):
        for (left_at, left), (right_at, right) in zip(points, points[1:]):
            if left_at <= second <= right_at:
                return left + (right - left) * (second - left_at) / (right_at - left_at)
        return points[-1][1]

    def _shape(self, kind, onset, until, quantity=Quantity.HUMIDITY):
        return {"kind": kind, "onset": onset, "until": until, "quantity": quantity}

    def _math_curve(self, shapes, samples, *, policy, onset_at=None,
                    direction=None, onset_not_before=None):
        """Return declared local witnesses, retaining the real original support."""
        if not samples:
            return None
        midpoint = (self.temperature + self.baseline) / 2
        quantity = (Quantity.TEMPERATURE if samples[0][1] > midpoint
                    else Quantity.HUMIDITY)
        compatible = []
        for shape in shapes:
            sign = -1 if shape["kind"] in ("open", "decay") else 1
            onset = self._at(shape["onset"])
            if (
                shape["quantity"] != quantity or direction != sign
                or (onset_at is not None and onset != onset_at)
                or (onset_not_before is not None and onset < onset_not_before)
                or onset not in {stamp for stamp, _ in samples}
            ):
                continue
            before = sum(stamp <= onset for stamp, _ in samples)
            if min(before, len(samples) - before) >= policy.min_side_samples:
                compatible.append((onset, shape, before))
        if not compatible:
            return None
        onset, shape, before = max(compatible, key=lambda item: item[0])
        before_slope, after_slope = {
            "rise": (0.0, 1.0), "decay": (1.0, -1.0),
            "open": (0.0, -1.0), "close": (-1.0, 1.0),
        }[shape["kind"]]
        change = after_slope - before_slope
        return CurveFeatures(
            evidence=("supports" if samples[-1][0] <= self._at(shape["until"])
                      else "rejects"),
            onset_at=onset, newest_at=samples[-1][0], before_count=before,
            after_count=len(samples) - before, noise_scale=1.0,
            slope_before=before_slope, slope_after=after_slope,
            slope_change=change, signed_strength=change,
            smooth_score=1.0, change_score=0.0, score_gain=1.0,
            smooth_model="linear",
            after_trend=CurveTrendFeatures(
                direction=-1 if shape["kind"] in ("open", "decay") else 1,
                slope=after_slope, noise_scale=1.0,
                level_score=1.0, trend_score=0.0, score_gain=1.0,
                exact_constant=False, exact_linear=False,
            ),
        )

    @staticmethod
    def _math_level(samples, *, policy):
        """Only an exactly settled supplied tail receives level evidence."""
        if len(samples) < 2 * policy.min_side_samples:
            return None
        constant = all(value == samples[0][1] for _, value in samples)
        return CurveLevelFeatures(
            evidence="supports" if constant else "rejects",
            start_at=samples[0][0], newest_at=samples[-1][0], count=len(samples),
            noise_scale=0.0 if constant else 1.0,
            level=sum(value for _, value in samples) / len(samples),
            level_score=None if constant else 1.0,
            trend_score=None if constant else 0.0,
            score_gain=None if constant else -1.0,
            trend_model=None if constant else "linear", exact_constant=constant,
        )

    def _patch_math(self, shapes):
        return patch(
            "custom_components.ha_sauna.core.detector.curve_change",
            side_effect=lambda samples, **kwargs: self._math_curve(shapes, samples, **kwargs),
        )

    def _send(self, detector, second, humidity, events, *, temperature=None,
              measured_second=None, humidity_source=None, controller=None):
        temperature = self.temperature if temperature is None else temperature
        measured_at = self._at(second if measured_second is None else measured_second)
        now = self._at(second)
        if controller is not None:
            controller.set_temperature(temperature, now)
        for position in detector.positions:
            position_humidity = humidity[position] if isinstance(humidity, dict) else humidity
            for quantity, value in (
                (Quantity.TEMPERATURE, temperature), (Quantity.HUMIDITY, position_humidity),
            ):
                source = humidity_source if quantity == Quantity.HUMIDITY else None
                self.assertTrue(detector.accept(measurement(
                    position, quantity, value, second, source=source, measured=measured_at,
                )))

        def received(detection):
            controller.process(Event(
                f"detected:{detection.kind}:{detection.trace_at}", "s", detection.kind,
                detection.effective_at, detection.detected_at,
                booking_at=detection.trace_at, trace_at=detection.trace_at,
            ), recognition_at=detection.trace_at)

        events.extend(detector.advance(
            now, enabled=True,
            allowed=controller.recognition_allowed if controller is not None else None,
            allowed_at=controller.recognition_allowed_at if controller is not None else None,
            recognition_context=(
                controller.recognition_context_at if controller is not None else None
            ),
            onset_allowed=(
                controller.detection_onset_allowed if controller is not None else None
            ),
            on_detection=received if controller is not None else None,
        ))

    def _feed(self, detector, through, points, events, *, humidity_source=None,
              temperatures=None, controller=None):
        second = (detector.index + 1) * self.step
        while second <= through:
            humidity = (
                {position: self._value(values, second) for position, values in points.items()}
                if isinstance(points, dict) else self._value(points, second)
            )
            self._send(
                detector, second, humidity, events,
                humidity_source=humidity_source, controller=controller,
                temperature=(self._value(temperatures, second)
                             if temperatures is not None else None),
            )
            second += self.step

    @staticmethod
    def _infusions(events):
        return [item for item in events if item.kind == Kind.INFUSION]

    def _scenario(self, *, merged):
        peak_one = self.begin + self.width
        fall_one = peak_one + self.width
        rise_two = (
            fall_one - self.side * self.step if merged else
            fall_one + self.p["infusion_window_seconds"] + (self.side + 3) * self.step
        )
        peak_two = rise_two + self.width
        fall_two = peak_two + self.width
        points = [(0, self.baseline), (self.begin, self.baseline), (peak_one, self.peak)]
        if merged:
            partial = self.peak + (self.level - self.peak) * (rise_two - peak_one) / self.width
            points.append((rise_two, partial))
        else:
            points.extend(((fall_one, self.level), (rise_two, self.level)))
        points.extend(((peak_two, self.peak), (fall_two, self.level)))
        shapes = [
            self._shape("rise", self.begin, peak_one),
            self._shape("decay", peak_one, rise_two - self.step if merged else fall_one),
            self._shape("rise", rise_two, peak_two),
            self._shape("decay", peak_two, fall_two),
        ]
        return points, shapes, rise_two, fall_two

    def test_monotone_rise_has_no_infusion_and_keeps_person_detection_available(self):
        end = self.begin + ceil(
            self.p["strong_window_seconds"] + self.p["strong_hold_seconds"]
            + self.p["median_seconds"] + 2 * self.p["person_step_seconds"]
        )
        rate = 2 * max(
            self.p["strong_humidity_upper"], self.p["strong_humidity_lower"],
        ) / 60
        points = [(0, self.baseline), (self.begin, self.baseline),
                  (end, self.baseline + rate * (end - self.begin))]
        shapes = [self._shape("rise", self.begin, end)]
        detector = Detector(self.parameters, T0)
        events = []
        with self._patch_math(shapes), patch(
            "custom_components.ha_sauna.core.detector.curve_level", side_effect=self._math_level,
        ):
            self._feed(detector, self.begin + (self.side + 1) * self.step, points, events)
            self.assertEqual(detector._infusion_episode["phase"], "pending")
            self.assertIsNone(detector._infusion_episode["confirmed_at"])
            self.assertTrue(all(
                witness["change"] > 0 for witness in detector._infusion_episode["water"].values()
            ))
            self._feed(detector, end, points, events)
        self.assertEqual(self._infusions(events), [])
        self.assertIn(Kind.PERSON_STRONG, [item.kind for item in events])

    def test_expired_pending_rise_cannot_own_a_later_decay_or_new_pulse(self):
        old_peak = self.begin + self.width
        expired = self.begin + self.p["curve_window_seconds"] + self.step
        unrelated_peak = expired + self.step
        unrelated_fall = unrelated_peak + self.width
        next_rise = (
            unrelated_fall + self.p["infusion_window_seconds"] + (self.side + 3) * self.step
        )
        next_peak = next_rise + self.width
        next_fall = next_peak + self.width
        points = [
            (0, self.baseline), (self.begin, self.baseline), (old_peak, self.peak),
            (unrelated_peak, self.peak), (unrelated_fall, self.level),
            (next_rise, self.level), (next_peak, self.peak), (next_fall, self.level),
        ]
        shapes = [
            self._shape("rise", self.begin, old_peak),
            self._shape("decay", unrelated_peak, unrelated_fall),
            self._shape("rise", next_rise, next_peak),
            self._shape("decay", next_peak, next_fall),
        ]
        detector = Detector(self.parameters, T0, (Position.UPPER,))
        events = []
        key = ("infusion", detector.positions, (Quantity.HUMIDITY,))
        with self._patch_math(shapes), patch(
            "custom_components.ha_sauna.core.detector.curve_level", side_effect=self._math_level,
        ):
            self._feed(detector, self.begin + (self.side + 1) * self.step, points, events)
            self.assertEqual(detector._infusion_episode["phase"], "pending")
            old = dict(detector._curve_proposal_status[key])
            self._feed(detector, expired, points, events)
            self.assertIsNone(detector._infusion_episode)
            self.assertEqual(self._infusions(events), [])
            self.assertEqual(detector._curve_proposal_status[key]["status"], "invalid")
            self.assertEqual(detector._curve_proposal_status[key]["reason"], "candidate_expired")
            self._feed(detector, unrelated_fall, points, events)
            self.assertIsNone(detector._infusion_episode)
            self.assertEqual(self._infusions(events), [])
            first_rise = next_rise + self.side * self.step
            self._feed(detector, first_rise, points, events)
            rise = detector.diagnostic["curve_candidates"]["infusion"]["upper"]["humidity"]
            self.assertEqual(rise["onset_at"], self._at(next_rise))
            self.assertIsNone(rise["fresh_newest_at"])
            self.assertIsNone(detector._infusion_episode)
            self._feed(detector, first_rise + self.step, points, events)
            self.assertEqual(detector._infusion_episode["phase"], "pending")
            self.assertEqual(detector._infusion_episode["onset_at"], self._at(next_rise))
            self.assertTrue(set(old["candidate_ids"]).isdisjoint(
                detector._curve_proposal_status[key]["candidate_ids"],
            ))
            first_decay = next_peak + self.side * self.step
            self._feed(detector, first_decay, points, events)
            decay = detector.diagnostic["curve_candidates"]["infusion_decay"]["upper"]["humidity"]
            self.assertIsNone(decay["fresh_newest_at"])
            self.assertEqual(self._infusions(events), [])
            self._feed(detector, next_fall, points, events)
        infusions = self._infusions(events)
        self.assertEqual(len(infusions), 1)
        self.assertEqual(infusions[0].effective_at, self._at(next_rise))
        self.assertGreater(infusions[0].trace_at, self._at(first_decay))

    def test_global_rise_without_supported_original_direction_cannot_start_a_pulse(self):
        points, shapes, _, _ = self._scenario(merged=False)
        level = CurveTrendFeatures(
            direction=0, slope=0.0, noise_scale=0.0,
            level_score=None, trend_score=None, score_gain=None,
            exact_constant=True, exact_linear=False,
        )
        for trend in (None, level):
            with self.subTest(after_trend=trend):
                detector = Detector(self.parameters, T0)
                events = []

                def evidence(samples, **kwargs):
                    features = self._math_curve(shapes, samples, **kwargs)
                    return (replace(features, after_trend=trend)
                            if features is not None else None)

                with patch(
                    "custom_components.ha_sauna.core.detector.curve_change", side_effect=evidence,
                ):
                    self._feed(detector, self.begin + self.width, points, events)
                self.assertEqual(self._infusions(events), [])
                self.assertIsNone(detector._infusion_episode)

    def test_peak_and_fresh_fall_confirm_the_original_onset_for_all_consumers(self):
        points, shapes, _, _ = self._scenario(merged=False)
        peak = self.begin + self.width
        detector = Detector(self.parameters, T0)
        controller = Controller(self.parameters)
        controller.set_temperature(self.temperature, T0)
        controller.begin_session("s", T0)
        controller.process(event("initial-open", Kind.DOOR_OPEN, 0))
        controller.process(event("initial-close", Kind.DOOR_CLOSE, 0))
        events = []
        with self._patch_math(shapes), patch(
            "custom_components.ha_sauna.core.detector.curve_level", side_effect=self._math_level,
        ):
            self._feed(detector, peak, points, events, controller=controller)
            self.assertEqual(detector._infusion_episode["phase"], "pending")
            self.assertEqual(self._infusions(events), [])
            self.assertEqual(controller.session.timeline.gang_count, 0)
            self.assertFalse(controller.regulation_inputs.gang_heat_demand)
            self.assertFalse(any(item.kind == "infusion" for item in controller.consumer_events))
            self._feed(detector, peak + self.width, points, events, controller=controller)
        infusions = self._infusions(events)
        self.assertEqual(len(infusions), 1)
        infusion = infusions[0]
        self.assertEqual(infusion.effective_at, self._at(self.begin))
        self.assertGreater(infusion.trace_at, self._at(peak))
        self.assertEqual(infusion.detected_at, infusion.trace_at)
        self.assertEqual(detector._infusion_episode["confirmed_at"], infusion.trace_at)
        gang = controller.session.timeline.active
        self.assertIsNotNone(gang)
        self.assertEqual(len(gang.infusion_events), 1)
        stored = gang.infusion_events[0]
        self.assertEqual(stored.effective_at, infusion.effective_at)
        self.assertEqual(stored.booking_at, infusion.trace_at)
        self.assertEqual(stored.trace_at, infusion.trace_at)
        self.assertEqual(gang.confirmed_at, infusion.trace_at)
        consumed = [item for item in controller.consumer_events if item.kind == "infusion"]
        self.assertEqual(len(consumed), 1)
        self.assertEqual(consumed[0].effective_at, infusion.effective_at)
        self.assertEqual(consumed[0].received_at, infusion.detected_at)
        self.assertTrue(controller.regulation_inputs.gang_heat_demand)

    def test_multiple_peaks_before_level_end_remain_one_infusion(self):
        points, shapes, rise_two, fall_two = self._scenario(merged=True)
        detector = Detector(self.parameters, T0)
        events = []
        with self._patch_math(shapes), patch(
            "custom_components.ha_sauna.core.detector.curve_level", side_effect=self._math_level,
        ):
            self._feed(detector, rise_two, points, events)
            self.assertEqual(len(self._infusions(events)), 1)
            self.assertEqual(detector._infusion_episode["phase"], "active")
            self._feed(
                detector, fall_two + self.p["infusion_window_seconds"] + 2 * self.step,
                points, events,
            )
        self.assertEqual(len(self._infusions(events)), 1)
        self.assertEqual(detector._infusion_episode["phase"], "ended")
        self.assertEqual(detector._infusion_episode["end_basis"], "decay_and_level")

    def test_upper_peak_preserves_lower_tail_until_shared_level_and_next_infusion(self):
        first_peak = self.begin + self.width
        first_fall = first_peak + self.width
        upper_rise = first_fall + (self.side + 1) * self.step
        upper_peak = upper_rise + self.width
        upper_fall = upper_peak + self.width
        settled = upper_fall + self.p["infusion_window_seconds"] + 2 * self.step
        next_rise = settled + (self.side + 3) * self.step
        next_peak = next_rise + self.width
        next_fall = next_peak + self.width
        upper_points = [
            (0, self.baseline), (self.begin, self.baseline), (first_peak, self.peak),
            (first_fall, self.level), (upper_rise, self.level), (upper_peak, self.peak),
            (upper_fall, self.level), (next_rise, self.level), (next_peak, self.peak),
            (next_fall, self.level),
        ]
        # Disjoint valid RH ranges identify the two original inputs to the
        # controlled math; the lower input has no intermediate rise or fall.
        lower_points = [
            (0, self.baseline / 2), (self.begin, self.baseline / 2),
            (first_peak, self.peak / 2), (first_fall, self.level / 2),
            (next_rise, self.level / 2), (next_peak, self.peak / 2),
            (next_fall, self.level / 2),
        ]
        points = {Position.UPPER: upper_points, Position.LOWER: lower_points}
        common_shapes = [
            self._shape("rise", self.begin, first_peak),
            self._shape("decay", first_peak, first_fall),
            self._shape("rise", next_rise, next_peak),
            self._shape("decay", next_peak, next_fall),
        ]
        upper_shapes = common_shapes + [
            self._shape("rise", upper_rise, upper_peak),
            self._shape("decay", upper_peak, upper_fall),
        ]

        def evidence(samples, **kwargs):
            shapes = (common_shapes if samples and samples[0][1] <= self.peak / 2
                      else upper_shapes)
            return self._math_curve(shapes, samples, **kwargs)

        for paused in (False, True):
            with self.subTest(paused=paused):
                detector = Detector(self.parameters, T0)
                controller = Controller(self.parameters)
                controller.set_temperature(self.temperature, T0)
                controller.begin_session("s", T0)
                controller.process(event("initial-open", Kind.DOOR_OPEN, 0))
                controller.process(event("initial-close", Kind.DOOR_CLOSE, 0))
                events = []
                with patch(
                    "custom_components.ha_sauna.core.detector.curve_change", side_effect=evidence,
                ), patch(
                    "custom_components.ha_sauna.core.detector.curve_level",
                    side_effect=self._math_level,
                ):
                    self._feed(detector, first_fall - self.step, points, events,
                               controller=controller)
                    self.assertEqual(len(self._infusions(events)), 1)
                    self.assertEqual(detector._infusion_episode["phase"], "active")
                    lower_decay = detector._infusion_episode["decays"][Position.LOWER]
                    self.assertEqual(lower_decay.onset_at, self._at(first_peak))
                    first_gang = controller.session.timeline.active
                    self.assertIsNotNone(first_gang)
                    self.assertEqual(len(first_gang.infusion_events), 1)
                    if paused:
                        # A global gap removes both decays. Afterwards the lower
                        # position can recover a fresh level without a new peak.
                        self._send(detector, first_fall, None, events, controller=controller)
                    self._feed(detector, upper_peak, points, events, controller=controller)
                    episode = detector._infusion_episode
                    self.assertEqual(episode["phase"], "active")
                    self.assertIsNone(episode["level_end_at"])
                    self.assertNotIn(Position.UPPER, episode["decays"])
                    self.assertEqual(len(self._infusions(events)), 1)
                    self.assertEqual(episode["onset_at"], self._at(self.begin))
                    if not paused:
                        preserved = episode["decays"][Position.LOWER]
                        self.assertEqual(preserved.candidate_id, lower_decay.candidate_id)
                        self.assertEqual(preserved.original_points, lower_decay.original_points)
                    lower_level = episode["levels"][Position.LOWER]
                    self.assertIsNotNone(lower_level["fresh_newest_at"])
                    self.assertEqual(len(controller.session.timeline.active.infusion_events), 1)
                    self.assertEqual(len([item for item in controller.consumer_events
                                          if item.kind == "infusion"]), 1)
                    # The upper position still needs its own falling originals
                    # and a fresh level before the shared episode can end.
                    self._feed(detector, settled, points, events, controller=controller)
                    episode = detector._infusion_episode
                    self.assertEqual(episode["phase"], "ended")
                    self.assertEqual(episode["decays"][Position.UPPER].onset_at, self._at(upper_peak))
                    self.assertGreater(episode["level_end_at"], self._at(upper_peak))
                    for position in detector.positions:
                        level = episode["levels"][position]
                        self.assertGreater(level["fresh_newest_at"], level["first_newest_at"])
                    self.assertEqual(len(self._infusions(events)), 1)
                    self._feed(detector, next_fall, points, events, controller=controller)
                infusions = self._infusions(events)
                self.assertEqual(len(infusions), 2)
                self.assertEqual([item.effective_at for item in infusions],
                                 [self._at(self.begin), self._at(next_rise)])
                self.assertTrue(all(item.channels == ("upper", "lower") for item in infusions))
                self.assertTrue(all(item.effective_at < item.trace_at for item in infusions))
                gang = controller.session.timeline.active
                self.assertIsNotNone(gang)
                self.assertEqual(gang.gang_id, first_gang.gang_id)
                self.assertEqual(controller.session.timeline.gang_count, 1)
                self.assertEqual([item.effective_at for item in gang.infusion_events],
                                 [item.effective_at for item in infusions])
                self.assertEqual([item.trace_at for item in gang.infusion_events],
                                 [item.trace_at for item in infusions])
                consumed = [item for item in controller.consumer_events if item.kind == "infusion"]
                self.assertEqual([item.effective_at for item in consumed],
                                 [item.effective_at for item in infusions])
                self.assertEqual([item.received_at for item in consumed],
                                 [item.detected_at for item in infusions])

    def test_new_pulse_after_confirmed_level_counts_again(self):
        points, shapes, rise_two, fall_two = self._scenario(merged=False)
        settled = (self.baseline + self.peak) / 2
        self.assertLess(self.baseline, settled)
        self.assertLess(settled, self.peak)
        plateau_times = {self.begin + 2 * self.width, rise_two, fall_two}
        points = [
            (stamp, settled if stamp in plateau_times else value)
            for stamp, value in points
        ]
        detector = Detector(self.parameters, T0)
        events = []
        with self._patch_math(shapes), patch(
            "custom_components.ha_sauna.core.detector.curve_level", side_effect=self._math_level,
        ):
            self._feed(detector, rise_two - self.step, points, events)
            self.assertEqual(detector._infusion_episode["phase"], "ended")
            level_end = detector._infusion_episode["level_end_at"]
            self.assertLess(level_end, self._at(rise_two))
            self._feed(detector, fall_two, points, events)
        infusions = self._infusions(events)
        self.assertEqual(len(infusions), 2)
        self.assertEqual([item.effective_at for item in infusions],
                         [self._at(self.begin), self._at(rise_two)])
        self.assertTrue(all(item.effective_at < item.trace_at for item in infusions))

    def test_infusion_confirmation_needs_new_original_not_ticks_or_same_time_correction(self):
        points, shapes, _, _ = self._scenario(merged=False)
        detector = Detector(self.parameters, T0, (Position.UPPER,))
        events = []
        first = self.begin + self.side * self.step
        first_decay = self.begin + self.width + self.side * self.step
        confirmed = self.begin + 2 * self.width
        with self._patch_math(shapes), patch(
            "custom_components.ha_sauna.core.detector.curve_level", side_effect=self._math_level,
        ):
            self._feed(detector, first, points, events)
            self.assertEqual(self._infusions(events), [])
            status = detector.diagnostic["curve_candidates"]["infusion"]["upper"]["humidity"]
            self.assertEqual(status["first_newest_at"], self._at(first))
            self.assertIsNone(status["fresh_newest_at"])
            events.extend(detector.advance(self._at(first + self.step), enabled=True))
            self._send(detector, first + 2 * self.step, self._value(points, first), events,
                       measured_second=first)
            self._send(detector, first + 3 * self.step, self._value(points, first) + .1,
                       events, measured_second=first)
            self.assertEqual(self._infusions(events), [])
            self._send(detector, first + 4 * self.step,
                       self._value(points, first + 4 * self.step), events)
            self.assertEqual(detector._infusion_episode["phase"], "pending")
            self.assertEqual(self._infusions(events), [])
            self._feed(detector, first_decay, points, events)
            decay = detector.diagnostic["curve_candidates"]["infusion_decay"]["upper"]["humidity"]
            self.assertEqual(decay["first_newest_at"], self._at(first_decay))
            self.assertIsNone(decay["fresh_newest_at"])
            events.extend(detector.advance(self._at(first_decay + self.step), enabled=True))
            self._send(detector, first_decay + 2 * self.step,
                       self._value(points, first_decay), events, measured_second=first_decay)
            self._send(detector, first_decay + 3 * self.step,
                       self._value(points, first_decay) - .1, events,
                       measured_second=first_decay)
            self.assertEqual(detector._infusion_episode["phase"], "pending")
            self.assertEqual(self._infusions(events), [])
            events.extend(detector.advance(self._at(confirmed - self.step), enabled=True))
            self.assertEqual(self._infusions(events), [])
            self._send(detector, confirmed, self._value(points, confirmed), events)
        self.assertEqual(len(self._infusions(events)), 1)
        infusion = self._infusions(events)[0]
        self.assertEqual(infusion.effective_at, self._at(self.begin))
        self.assertEqual(infusion.trace_at, self._at(confirmed))
        self.assertEqual(infusion.detected_at, infusion.trace_at)
        self.assertEqual(detector._infusion_episode["phase"], "active")
        self.assertLessEqual(
            detector.diagnostic["metrics"]["upper"]["infusion_absolute_humidity_delta"], 0,
        )

    def test_level_end_needs_new_original_not_ticks_or_same_time_correction(self):
        points, shapes, _, _ = self._scenario(merged=False)
        detector = Detector(self.parameters, T0, (Position.UPPER,))
        events = []
        first = self.begin + 2 * self.width + self.p["infusion_window_seconds"]
        with self._patch_math(shapes), patch(
            "custom_components.ha_sauna.core.detector.curve_level", side_effect=self._math_level,
        ):
            self._feed(detector, first, points, events)
            witness = detector._infusion_episode["levels"][Position.UPPER]
            self.assertEqual(witness["first_newest_at"], self._at(first))
            self.assertIsNone(witness["fresh_newest_at"])
            events.extend(detector.advance(self._at(first + self.step), enabled=True))
            self._send(detector, first + 2 * self.step, self.level - .1, events,
                       measured_second=first)
            self._send(detector, first + 3 * self.step, self.level, events,
                       measured_second=first)
            self.assertEqual(detector._infusion_episode["phase"], "active")
            self.assertEqual(detector._infusion_episode["levels"], {})
            self._send(detector, first + 4 * self.step, self.level, events)
            self.assertEqual(detector._infusion_episode["phase"], "active")
            self.assertIsNone(detector._infusion_episode["level_end_at"])
            self._send(detector, first + 5 * self.step, self.level, events)
        self.assertEqual(detector._infusion_episode["phase"], "ended")
        self.assertEqual(len(self._infusions(events)), 1)

    def test_source_gap_can_recover_a_new_level_without_inventing_a_decay(self):
        first_peak = self.begin + self.width
        gap = first_peak + (self.side + 2) * self.step
        recovered = gap + self.step
        next_rise = recovered + self.p["infusion_window_seconds"] + (self.side + 3) * self.step
        next_peak = next_rise + self.width
        next_fall = next_peak + self.width
        points = [
            (0, self.baseline), (self.begin, self.baseline), (first_peak, self.peak),
            (recovered, self.level), (next_rise, self.level), (next_peak, self.peak),
            (next_fall, self.level),
        ]
        shapes = [self._shape("rise", self.begin, first_peak),
                  self._shape("decay", first_peak, gap - self.step),
                  self._shape("rise", next_rise, next_peak),
                  self._shape("decay", next_peak, next_fall)]
        observed = []
        detector = Detector(self.parameters, T0, observer=observed.append)
        events = []
        with self._patch_math(shapes), patch(
            "custom_components.ha_sauna.core.detector.curve_level", side_effect=self._math_level,
        ):
            self._feed(detector, gap - self.step, points, events)
            self._send(detector, gap, None, events)
            self.assertEqual(detector._infusion_episode["phase"], "active")
            self.assertTrue(detector._infusion_episode["interrupted"])
            self._feed(detector, next_rise - self.step, points, events,
                       humidity_source="sensor.recovered_humidity")
            self.assertEqual(detector._infusion_episode["phase"], "ended")
            self.assertEqual(detector._infusion_episode["end_basis"], "level_after_interruption")
            self.assertEqual(detector._infusion_episode["decays"], {})
            self.assertEqual(len(self._infusions(events)), 1)
            self._feed(detector, next_fall, points, events,
                       humidity_source="sensor.recovered_humidity")
        self.assertEqual(len(self._infusions(events)), 2)
        self.assertTrue(any(
            row["conditions"].get("infusion_level_after_interruption") is True
            for row in observed
        ))

    def test_door_context_assigns_a_new_infusion_to_the_next_real_gang(self):
        first_peak = self.begin + self.width
        first_fall = first_peak + self.width
        door_start = first_fall + (self.side + 2) * self.step
        door_bottom = door_start + self.width
        close_start = door_bottom + max(
            self.p["infusion_window_seconds"], self.p["median_seconds"],
        ) + self.side * self.step
        close_end = close_start + self.width
        next_rise = close_end + self.p["infusion_window_seconds"] + self.p["median_seconds"]
        next_peak = next_rise + self.width
        next_fall = next_peak + self.width
        # These inputs exceed the unchanged two-position ventilation losses.
        cold = self.temperature - 2 * max(self.p["vent_drop_upper"], self.p["vent_drop_lower"])
        dry = self.baseline * (1 - 2 * self.p["vent_absolute_humidity_loss_percent"] / 100)
        points = [
            (0, self.baseline), (self.begin, self.baseline), (first_peak, self.peak),
            (first_fall, self.level), (door_start, self.level),
            (door_bottom, dry), (close_start, dry),
            (close_end, self.level), (next_rise, self.level), (next_peak, self.peak),
            (next_fall, self.level),
        ]
        temperatures = [(0, self.temperature), (door_start, self.temperature),
                        (door_bottom, cold), (close_start, cold), (close_end, self.temperature)]
        shapes = [self._shape("rise", self.begin, first_peak),
                  self._shape("decay", first_peak, first_fall),
                  self._shape("rise", next_rise, next_peak),
                  self._shape("decay", next_peak, next_fall)]
        for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY):
            shapes.extend((self._shape("open", door_start, door_bottom, quantity),
                           self._shape("close", close_start, close_end, quantity)))
        detector = Detector(self.parameters, T0)
        controller = Controller(self.parameters)
        controller.set_temperature(self.temperature, T0)
        controller.begin_session("s", T0)
        controller.process(event("initial-open", Kind.DOOR_OPEN, 0))
        controller.process(event("initial-close", Kind.DOOR_CLOSE, 0))
        events = []
        with self._patch_math(shapes), patch(
            "custom_components.ha_sauna.core.detector.curve_level", side_effect=self._math_level,
        ):
            self._feed(detector, door_start - self.step, points, events,
                       temperatures=temperatures, controller=controller)
            self.assertEqual(detector._infusion_episode["phase"], "active")
            self.assertEqual(len(controller.session.timeline.active.infusion_events), 1)
            self._feed(detector, next_fall, points, events,
                       temperatures=temperatures, controller=controller)
        self.assertEqual(len(self._infusions(events)), 2)
        self.assertEqual([item.kind for item in events if item.kind in (
            Kind.DOOR_OPEN, Kind.VENTILATION, Kind.DOOR_CLOSE,
        )], [Kind.DOOR_OPEN, Kind.VENTILATION, Kind.DOOR_CLOSE])
        timeline = controller.session.timeline
        self.assertEqual(len(timeline.completed), 1)
        self.assertIsNotNone(timeline.active)
        self.assertNotEqual(timeline.completed[0].gang_id, timeline.active.gang_id)
        self.assertEqual(len(timeline.completed[0].infusion_events), 1)
        self.assertEqual(len(timeline.active.infusion_events), 1)

    def test_corrected_ended_tail_keeps_the_next_peak_in_the_same_infusion(self):
        points, shapes, _, _ = self._scenario(merged=False)
        ended = self.begin + 2 * self.width + self.p["infusion_window_seconds"] + self.step
        next_rise = ended + 3 * self.step
        next_peak = next_rise + self.width
        shapes = shapes[:2] + [self._shape("rise", next_rise, next_peak)]
        detector = Detector(self.parameters, T0, (Position.UPPER,))
        events = []
        with self._patch_math(shapes), patch(
            "custom_components.ha_sauna.core.detector.curve_level", side_effect=self._math_level,
        ):
            self._feed(detector, ended, points, events)
            self.assertEqual(detector._infusion_episode["phase"], "ended")
            self._send(detector, ended + self.step, self.level - .1, events,
                       measured_second=ended)
            self.assertEqual(detector._infusion_episode["phase"], "active")
            self.assertIsNone(detector._infusion_episode["level_end_at"])
            continued = [(0, self.level - .1), (next_rise, self.level - .1),
                         (next_peak, self.peak)]
            self._feed(detector, next_peak, continued, events)
        self.assertEqual(len(self._infusions(events)), 1)
        self.assertEqual(detector._infusion_episode["phase"], "active")
