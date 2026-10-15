"""Synthetische Zeitreihen prüfen Kausalität, Ausfall und Quellenwechsel."""
import asyncio
import unittest
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from math import ceil
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import DEFAULT, patch

from test_foundation import T0, event, parameters

from custom_components.ha_sauna.bindings import Bindings
from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.curve_change import (
    CurveFeatures,
    CurveTrendFeatures,
    OriginalPositionWindows,
    original_contrasts,
    original_water_pair,
)
from custom_components.ha_sauna.core.detector import Detector
from custom_components.ha_sauna.core.models import Measurement, Position, Quantity
from custom_components.ha_sauna.core.moisture import (
    WATER_VAPOR_GAS_CONSTANT,
    absolute_humidity,
    saturation_vapor_pressure,
)
from custom_components.ha_sauna.core.parameters import (
    BY_KEY,
    EDITABLE_DEFINITIONS,
    ParameterError,
    Parameters,
)
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

    def test_water_plateau_ventilation_ends_confirmed_gang_and_blocks_gang_heat(self):
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
        statistics = Controller(p)
        statistics.set_temperature(80, T0)
        statistics.begin_session("statistics", T0)
        d.report_heating(True, T0)
        found = []
        statistics_heat = []

        def received(detection):
            found.append(detection)
            c.process(Event(f"detected:{detection.kind}:{detection.trace_at}", "s",
                            detection.kind, detection.effective_at,
                            detection.detected_at, detection.trace_at))
            before_heat = statistics.last_decision.heat
            statistics.process(Event(
                f"statistics:{detection.kind}:{detection.trace_at}", "statistics",
                detection.kind, detection.effective_at, detection.detected_at, detection.trace_at,
            ))
            if detection.kind == Kind.VENTILATION:
                statistics_heat.append((before_heat, statistics.last_decision.heat))

        _, plateau, end, values = self._ventilation_curve(d)
        for second in range(0, end + 1, d.grid_seconds):
            now = T0 + timedelta(seconds=second)
            temperature, humidity = values(second)
            c.set_temperature(temperature, now)
            statistics.set_temperature(temperature, now)
            for position in d.positions:
                d.accept(measurement(position, Quantity.TEMPERATURE, temperature, second))
                d.accept(measurement(position, Quantity.HUMIDITY, humidity, second))
            d.advance(now, enabled=True, allowed=c.recognition_allowed,
                      on_detection=received, recognition_context=c.recognition_context_at)

        self.assertEqual(d.capacity, p.values["door_humidity_seconds"] + p.values["vent_baseline_seconds"] + 1)
        ventilation = [e for e in found if e.kind == Kind.VENTILATION]
        self.assertEqual(len(ventilation), 1)
        self.assertGreater(ventilation[0].trace_at, T0 + timedelta(seconds=plateau))
        self.assertNotIn(Kind.DOOR_CLOSE, [e.kind for e in found])
        self.assertIsNone(c.session.timeline.active)
        self.assertEqual(c.session.timeline.gang_count, 1)
        self.assertFalse(c.regulation_inputs.gang_heat_demand)
        self.assertEqual(sum(e.kind == "gang_ended" for e in c.consumer_events), 1)
        self.assertEqual(c.session.timeline.completed[0].end_reason, "ventilation")
        self.assertEqual(c.session.timeline.completed[0].ended_at, ventilation[0].trace_at)
        self.assertIsNotNone(c.session.after_run)
        self.assertEqual(c.session.after_run.requested_at, ventilation[0].trace_at)
        self.assertFalse(c.last_decision.heat)
        self.assertEqual(sum(item.kind == Kind.VENTILATION
                             for item in statistics.session.timeline.processed), 1)
        self.assertIsNone(statistics.session.timeline.active)
        self.assertEqual(statistics.session.timeline.gang_count, 0)
        self.assertEqual(statistics.session.timeline.completed, ())
        self.assertIsNone(statistics.session.after_run)
        self.assertEqual(statistics.session.after_run_history, ())
        self.assertEqual(len(statistics_heat), 1)
        self.assertEqual(statistics_heat[0][0], statistics_heat[0][1])
        self.assertFalse(any(item.kind in ("gang_started", "gang_ended")
                             for item in statistics.consumer_events))

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

    def _ventilation_curve(self, detector, *, water_tail="plateau", temperature_tail="fall"):
        """Existing opening endpoints, followed by water rather than RH forms."""
        count, step = detector.p["curve_original_samples"], detector.grid_seconds
        begin, width = (2 * count + 1) * step, (2 * count + 2) * step
        plateau, end = begin + width, begin + 2 * width
        initial_t, initial_h = 80, 40
        fallen_t, fallen_h = 78, 38
        initial_water = absolute_humidity(initial_t, initial_h)
        fallen_water = absolute_humidity(fallen_t, fallen_h)

        def values(second):
            elapsed = max(0, second - begin)
            temperature = initial_t + (fallen_t - initial_t) * elapsed / width
            if elapsed > width and temperature_tail != "fall":
                temperature = (fallen_t if temperature_tail == "plateau" else
                               fallen_t + (initial_t - fallen_t) * (elapsed - width) / width)
            if elapsed > width and water_tail == "rh_plateau":
                return temperature, fallen_h
            water_elapsed = elapsed if water_tail == "fall" else min(elapsed, width)
            water = initial_water + (fallen_water - initial_water) * water_elapsed / width
            return temperature, self._humidity_for_water(temperature, water)

        return int(begin), int(plateau), int(end), values

    def _feed_ventilation(self, detector, through, values, *, positions=None,
                          missing=(), source=None):
        positions = detector.positions if positions is None else positions
        output = []
        for index in range(detector.index + 1, through // detector.grid_seconds + 1):
            second = index * detector.grid_seconds
            now = T0 + timedelta(seconds=second)
            for position in positions:
                for quantity, value in zip(
                    (Quantity.TEMPERATURE, Quantity.HUMIDITY), values(second), strict=True,
                ):
                    detector.accept(measurement(
                        position, quantity, None if (position, quantity) in missing else value,
                        second, source=source, measured=now,
                    ))
            output += detector.advance(now, enabled=True)
        return output

    def _segment(self, detector, duration, before, after=None, *, positions=None,
                 missing=(), heating=False, source=None, stop_when=None):
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
            if stop_when is not None and stop_when():
                break
        return output

    @staticmethod
    def _settling_time(detector):
        return ceil(max(detector.p["weak_window_seconds"], detector.p["strong_window_seconds"],
                        detector.p["vent_baseline_seconds"]) + detector.p["median_seconds"]
                    + max(detector.p["door_window_seconds"], detector.p["door_humidity_seconds"]))

    def _open_for_ventilation(self, detector, *, positions=None, heating=False):
        """Short opening followed by a temperature plateau, without continuing cooling."""
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
        events = self._segment(detector, self._settling_time(detector), (80, 40))

        def close_bound():
            return bool(detector.open and detector.door_episode is not None
                        and detector.door_episode.get("original_close"))

        # Braking can bind B before the warm recovery starts. Stop the same
        # opening input at its actual candidate, before the next original.
        for duration, before, after in (
            (20, (80, 40), (78, 38)),
            (detector.p["median_seconds"], (78, 38), (78, 38)),
            (30, (78, 38), (80, 38)),
        ):
            events += self._segment(detector, duration, before, after,
                                    stop_when=close_bound)
            if close_bound():
                break
        self.assertIn(Kind.DOOR_OPEN, [item.kind for item in events])
        self.assertTrue(detector.open)
        self.assertTrue(close_bound())
        self.assertTrue(detector.diagnostic["conditions"].get("door_close"))
        self.assertNotIn(Kind.DOOR_CLOSE, [item.kind for item in events])
        temperature = detector.frames[Position.UPPER][-1]["T"]
        gap_at = detector.index + 1
        for position in detector.positions:
            detector.accept(measurement(position, Quantity.TEMPERATURE, None, gap_at))
        events += detector.advance(T0 + timedelta(seconds=gap_at), enabled=True)
        self.assertEqual(detector.diagnostic["holds"].get("door", 0), 0)
        self.assertTrue(detector.open)
        self.assertNotIn("original_close", detector.door_episode)
        events += sample(detector, gap_at + 1, temperature + .01, 38)
        self.assertTrue(detector.open)
        self.assertNotIn("original_close", detector.door_episode)
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


    def test_water_plateau_with_falling_temperature_confirms_for_original_positions(self):
        for positions in ((Position.UPPER,), (Position.UPPER, Position.LOWER)):
            with self.subTest(positions=positions):
                detector = self._ventilation_detector(positions=positions)
                _, plateau, end, values = self._ventilation_curve(detector)
                events = self._feed_ventilation(detector, plateau, values)
                self.assertTrue(detector.open)
                self.assertNotIn(Kind.VENTILATION, [item.kind for item in events])
                events += self._feed_ventilation(detector, end, values)
                ventilation = [item for item in events if item.kind == Kind.VENTILATION]
                self.assertEqual(len(ventilation), 1)
                self.assertEqual(ventilation[0].channels,
                                 tuple(position.value for position in positions))
                self.assertGreater(ventilation[0].trace_at, T0 + timedelta(seconds=plateau))
                self.assertEqual(ventilation[0].effective_at, ventilation[0].trace_at)
                self.assertTrue(detector.ventilated)
                self.assertNotIn(Kind.DOOR_CLOSE, [item.kind for item in events])

    def test_continued_water_loss_and_flat_rh_are_not_a_water_plateau(self):
        prepared = self._ventilation_detector()
        _, plateau, end, values = self._ventilation_curve(prepared)
        events = self._feed_ventilation(prepared, plateau, values)
        self.assertTrue(prepared.open)
        self.assertNotIn(Kind.VENTILATION, [item.kind for item in events])
        for tail in ("fall", "rh_plateau"):
            with self.subTest(tail=tail):
                detector = deepcopy(prepared, {id(prepared.p): prepared.p})
                _, _, _, continued = self._ventilation_curve(detector, water_tail=tail)
                found = self._feed_ventilation(detector, end, continued)
                self.assertNotIn(Kind.VENTILATION, [item.kind for item in found])
                self.assertFalse(detector.ventilated)
                final_t, final_h = continued(end)
                plateau_t, plateau_h = continued(plateau)
                self.assertLess(final_t, plateau_t)
                self.assertLess(absolute_humidity(final_t, final_h),
                                absolute_humidity(plateau_t, plateau_h))
                if tail == "rh_plateau":
                    self.assertEqual(final_h, plateau_h)


    def test_gap_pauses_two_position_ventilation_and_same_sources_can_resume_it(self):
        detector = self._ventilation_detector()
        _, plateau, end, values = self._ventilation_curve(detector)
        pending_at = plateau + (detector.p["curve_original_samples"] - 1) * detector.grid_seconds
        events = self._feed_ventilation(detector, pending_at, values)
        self.assertIn("original_ventilation", detector.door_episode)
        opening, old_epochs = detector.door_episode, detector._original_context()
        gap = pending_at + detector.grid_seconds
        events += self._feed_ventilation(detector, gap, values, missing=(
            (Position.LOWER, Quantity.TEMPERATURE), (Position.LOWER, Quantity.HUMIDITY),
        ))
        self.assertNotIn("original_ventilation", detector.door_episode)
        self.assertIs(detector.door_episode, opening)
        self.assertNotEqual(detector._original_context(), old_epochs)
        events += self._feed_ventilation(detector, end, values)
        self.assertNotIn(Kind.VENTILATION, [item.kind for item in events])
        # A plateau after the gap cannot reuse the old falling window. Replay
        # only the new physical fall and plateau, shifted from the current pair.
        begin, _, new_end, fresh = self._ventilation_curve(detector)
        anchor_t, anchor_h = values(end)
        fresh_t, fresh_h = fresh(begin)
        water_scale = absolute_humidity(anchor_t, anchor_h) / absolute_humidity(fresh_t, fresh_h)

        def reacquired(second):
            temperature, humidity = fresh(second - end + begin)
            shifted_t = temperature + anchor_t - fresh_t
            water = absolute_humidity(temperature, humidity) * water_scale
            return shifted_t, self._humidity_for_water(shifted_t, water)

        events += self._feed_ventilation(detector, end + new_end - begin, reacquired)
        self.assertEqual([item.kind for item in events].count(Kind.VENTILATION), 1)
        self.assertIs(detector.door_episode, opening)
        self.assertEqual(detector.door_episode["positions"], detector.positions)


    def test_delayed_humidity_fall_opens_without_inventing_a_water_plateau(self):
        detector = self._ventilation_detector()
        events = self._segment(detector, self._settling_time(detector), (80, 40))
        events += self._segment(detector, 20, (80, 40), (78, 40))
        # The temperature hint exists before the later humidity loss qualifies the mixed route.
        events += self._segment(detector, 20, (78, 40), (76, 38))
        self.assertTrue(detector.open)
        self.assertEqual(detector.door_episode["positions"], detector.positions)
        self.assertNotIn(Kind.VENTILATION, [item.kind for item in events])


    def test_temperature_plateau_or_recovery_does_not_confirm_ventilation(self):
        prepared = self._ventilation_detector()
        _, plateau, end, values = self._ventilation_curve(prepared)
        self._feed_ventilation(prepared, plateau, values)
        for tail in ("plateau", "recovery"):
            with self.subTest(temperature=tail):
                detector = deepcopy(prepared, {id(prepared.p): prepared.p})
                _, _, _, counterform = self._ventilation_curve(detector, temperature_tail=tail)
                found = self._feed_ventilation(detector, end, counterform)
                self.assertNotIn(Kind.VENTILATION, [item.kind for item in found])
                if tail == "recovery":
                    self.assertIn(Kind.DOOR_CLOSE, [item.kind for item in found])


    def test_oscillating_water_cannot_supply_a_constant_plateau(self):
        detector = self._ventilation_detector(positions=(Position.UPPER,))
        begin, plateau, end, values = self._ventilation_curve(detector)
        events = self._feed_ventilation(detector, plateau, values)
        initial_water = absolute_humidity(*values(begin))
        settled_water = absolute_humidity(*values(plateau))
        count = detector.p["curve_original_samples"]

        def oscillating(second):
            temperature, _ = values(second)
            offset = ((second - plateau) // detector.grid_seconds - 1) % count
            # A symmetric U-shaped window has zero fitted slope while its
            # measured water values cannot share a constant uncertainty band.
            water = settled_water + (initial_water - settled_water) * (
                offset - (count - 1) / 2
            ) ** 2 / count
            return temperature, self._humidity_for_water(temperature, water)

        events += self._feed_ventilation(detector, end, oscillating)
        self.assertNotIn(Kind.VENTILATION, [item.kind for item in events])


    def test_runtime_positions_confirm_one_position_available_when_the_door_opens(self):
        """Die Runtime behält beide Rollen, obwohl unten vor der Öffnung fehlt."""
        prepared = Detector(parameters(), T0)
        _, plateau, end, values = self._ventilation_curve(prepared)
        initial = self._feed_ventilation(prepared, plateau, values, positions=(Position.UPPER,))
        self.assertEqual(prepared.positions, (Position.UPPER, Position.LOWER))
        self.assertTrue(prepared.open)
        self.assertEqual(prepared.door_episode["positions"], (Position.UPPER,))
        step = prepared.grid_seconds
        pending_at = plateau + (prepared.p["curve_original_samples"] - 1) * step
        lower_stamp = T0 + timedelta(seconds=plateau + step)
        lower_temperature = values(plateau)[0]
        lower_noise = prepared.p["curve_original_noise_temperature_lower"]

        for interleave in ("none", "unavailable", "same_stamp_correction"):
            with self.subTest(lower=interleave):
                detector = deepcopy(prepared, {id(prepared.p): prepared.p})
                opening = detector.door_episode
                events = list(initial)

                def report_lower(target, second):
                    if interleave == "none":
                        return
                    now = T0 + timedelta(seconds=second)
                    if interleave == "unavailable":
                        self.assertTrue(target.accept(measurement(
                            Position.LOWER, Quantity.TEMPERATURE, None, second, measured=now,
                        )))
                        return
                    if second == plateau + step:
                        self.assertTrue(target.accept(measurement(
                            Position.LOWER, Quantity.TEMPERATURE, lower_temperature, second,
                            measured=lower_stamp,
                        )))
                    self.assertTrue(target.accept(measurement(
                        Position.LOWER, Quantity.TEMPERATURE,
                        lower_temperature + (second - plateau) / step * lower_noise / 2,
                        second, measured=lower_stamp,
                    )))

                for second in range(plateau + step, end + step, step):
                    now = T0 + timedelta(seconds=second)
                    for quantity, value in zip(
                        (Quantity.TEMPERATURE, Quantity.HUMIDITY), values(second), strict=True,
                    ):
                        self.assertTrue(detector.accept(measurement(
                            Position.UPPER, quantity, value, second, measured=now,
                        )))
                    # An unbound role arrives after both fresh Upper originals.
                    report_lower(detector, second)
                    events += detector.advance(now, enabled=True)
                    if second != pending_at:
                        continue
                    self.assertIn("original_ventilation", opening)
                    self.assertNotIn(Kind.VENTILATION, [event.kind for event in events])
                    if interleave == "none":
                        continue
                    isolated = deepcopy(detector, {id(detector.p): detector.p})
                    candidate = isolated.door_episode["original_ventilation"]
                    received = second + step
                    report_lower(isolated, received)
                    found = isolated.advance(T0 + timedelta(seconds=received), enabled=True)
                    self.assertIs(isolated.door_episode["original_ventilation"], candidate)
                    self.assertNotIn(Kind.VENTILATION, [event.kind for event in found])
                    # A correction of the bound Upper role still discards it.
                    received += step
                    self.assertTrue(isolated.accept(measurement(
                        Position.UPPER, Quantity.HUMIDITY,
                        values(second)[1] + isolated.p["curve_original_noise_humidity_upper"] / 2,
                        received, measured=now,
                    )))
                    found += isolated.advance(T0 + timedelta(seconds=received), enabled=True)
                    self.assertNotIn("original_ventilation", isolated.door_episode)
                    self.assertNotIn(Kind.VENTILATION, [event.kind for event in found])

                ventilation = [event for event in events if event.kind is Kind.VENTILATION]
                self.assertEqual([event.kind for event in events].count(Kind.DOOR_OPEN), 1)
                self.assertEqual(len(ventilation), 1)
                self.assertEqual(ventilation[0].channels, ("upper",))
                self.assertIs(detector.door_episode, opening)
                self.assertEqual(opening["positions"], (Position.UPPER,))
                self.assertIn("lower_unavailable", detector.faults)

    def test_used_single_position_source_change_cannot_confirm_the_opening(self):
        detector = Detector(parameters(), T0)
        _, plateau, end, values = self._ventilation_curve(detector)
        events = self._feed_ventilation(detector, plateau, values, positions=(Position.UPPER,))
        events += self._feed_ventilation(detector, end, values, positions=(Position.UPPER,),
                                         source="sensor.upper_replaced")
        self.assertIn(Kind.DOOR_OPEN, [event.kind for event in events])
        self.assertNotIn(Kind.VENTILATION, [event.kind for event in events])
        self.assertTrue(detector.ventilation_degraded)

    def test_lost_position_during_open_door_cannot_fall_back_to_single_sensor_proof(self):
        detector = self._ventilation_detector()
        _, plateau, end, values = self._ventilation_curve(detector)
        events = self._feed_ventilation(detector, plateau, values)
        events += self._feed_ventilation(detector, end + detector.p["sensor_timeout_seconds"],
                                         values, positions=(Position.UPPER,))
        self.assertNotIn(Kind.VENTILATION, [item.kind for item in events])
        self.assertEqual(detector.door_episode["positions"], detector.positions)


    def test_source_replacement_during_opening_invalidates_its_reference(self):
        detector = self._ventilation_detector()
        _, plateau, end, values = self._ventilation_curve(detector)
        events = self._feed_ventilation(detector, plateau, values)
        events += self._feed_ventilation(detector, end, values, source="sensor.replaced")
        self.assertNotIn(Kind.VENTILATION, [item.kind for item in events])
        self.assertIn(Position.UPPER, detector.ventilation_invalid)

    def test_ventilation_needs_new_paired_originals_not_ticks_or_same_stamp_corrections(self):
        detector = self._ventilation_detector()
        _, plateau, _, values = self._ventilation_curve(detector)
        step = detector.grid_seconds
        pending_at = plateau + (detector.p["curve_original_samples"] - 1) * step
        found = self._feed_ventilation(detector, pending_at, values)
        self.assertIn("original_ventilation", detector.door_episode)
        self.assertNotIn(Kind.VENTILATION, [item.kind for item in found])
        for position in detector.positions:
            self.assertLessEqual(5 * step,
                                 detector.p[f"curve_original_gap_humidity_{position.value}_seconds"])
            self.assertLessEqual(4 * step,
                                 detector.p[f"curve_original_gap_temperature_{position.value}_seconds"])

        def report(received, measured, quantities, *, correction=False):
            pair = dict(zip((Quantity.TEMPERATURE, Quantity.HUMIDITY), values(measured), strict=True))
            for position in detector.positions:
                for quantity in quantities:
                    value = pair[quantity]
                    if correction and quantity == Quantity.HUMIDITY:
                        value += detector.p[f"curve_original_noise_humidity_{position.value}"] / 2
                    self.assertTrue(detector.accept(measurement(
                        position, quantity, value, received,
                        measured=T0 + timedelta(seconds=measured),
                    )))
            found.extend(detector.advance(T0 + timedelta(seconds=received), enabled=True))

        found += detector.advance(T0 + timedelta(seconds=pending_at + step), enabled=True)
        report(pending_at + 2 * step, pending_at, (Quantity.TEMPERATURE, Quantity.HUMIDITY))
        report(pending_at + 3 * step, pending_at, (Quantity.TEMPERATURE, Quantity.HUMIDITY),
               correction=True)
        self.assertNotIn(Kind.VENTILATION, [item.kind for item in found])
        report(pending_at + 4 * step, pending_at + 4 * step, (Quantity.TEMPERATURE,))
        self.assertNotIn(Kind.VENTILATION, [item.kind for item in found])
        # New RH has no right-hand T partner yet. It must remain pending rather
        # than supply freshness by skipping to a previously paired RH original.
        report(pending_at + 5 * step, pending_at + 5 * step, (Quantity.HUMIDITY,))
        self.assertNotIn(Kind.VENTILATION, [item.kind for item in found])
        report(pending_at + 6 * step, pending_at + 6 * step, (Quantity.TEMPERATURE,))
        ventilation = [item for item in found if item.kind == Kind.VENTILATION]
        self.assertEqual(len(ventilation), 1)
        self.assertEqual(ventilation[0].trace_at, T0 + timedelta(seconds=pending_at + 6 * step))

    def test_original_gap_discards_the_pending_water_plateau(self):
        detector = self._ventilation_detector()
        _, plateau, _, values = self._ventilation_curve(detector)
        step = detector.grid_seconds
        pending_at = plateau + (detector.p["curve_original_samples"] - 1) * step
        found = self._feed_ventilation(detector, pending_at, values)
        self.assertIn("original_ventilation", detector.door_episode)
        gap = max(detector.p[f"curve_original_gap_humidity_{position.value}_seconds"]
                  for position in detector.positions)
        received = pending_at + (ceil(gap / step) + 1) * step
        self.assertLess(received - pending_at, detector.p["sensor_timeout_seconds"])
        epochs = detector._original_context()
        for second in range(pending_at + step, received, step):
            temperature, _ = values(second)
            for position in detector.positions:
                detector.accept(measurement(position, Quantity.TEMPERATURE, temperature, second,
                                            measured=T0 + timedelta(seconds=second)))
            found += detector.advance(T0 + timedelta(seconds=second), enabled=True)
        found += self._feed_ventilation(detector, received, values)
        self.assertNotIn("original_ventilation", detector.door_episode)
        self.assertEqual(detector._original_context(), epochs)
        self.assertNotIn(Kind.VENTILATION, [item.kind for item in found])

    def test_earlier_ventilation_precedes_later_close_within_one_original_batch(self):
        detector = self._ventilation_detector()
        _, plateau, _, values = self._ventilation_curve(detector)
        count, step = detector.p["curve_original_samples"], detector.grid_seconds
        pending_at = plateau + (count - 1) * step
        found = self._feed_ventilation(detector, pending_at, values)
        self.assertIn("original_ventilation", detector.door_episode)
        self.assertNotIn(Kind.VENTILATION, [item.kind for item in found])
        interval = step / (2 * count + 4)
        first_at = pending_at + interval
        first_t, first_h = values(first_at)
        water = absolute_humidity(first_t, first_h)
        opening_temperature_loss = values(0)[0] - values(plateau)[0]
        for index in range(2 * count + 3):
            second = first_at + index * interval
            temperature = first_t + opening_temperature_loss * index / (2 * count + 2)
            humidity = self._humidity_for_water(temperature, water)
            for position in detector.positions:
                for quantity, value in ((Quantity.TEMPERATURE, temperature),
                                        (Quantity.HUMIDITY, humidity)):
                    self.assertTrue(detector.accept(measurement(
                        position, quantity, value, second,
                        measured=T0 + timedelta(seconds=second),
                    )))
        batch = detector.advance(T0 + timedelta(seconds=pending_at + step), enabled=True)
        doors = [item for item in batch if item.kind in (Kind.VENTILATION, Kind.DOOR_CLOSE)]
        self.assertEqual([item.kind for item in doors], [Kind.VENTILATION, Kind.DOOR_CLOSE])
        self.assertLessEqual(doors[0].effective_at, doors[1].effective_at)
        self.assertEqual(doors[0].trace_at, doors[1].trace_at)
        self.assertFalse(detector.open)
        self.assertIsNone(detector.door_episode)


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
            with self.subTest(base=base):
                detector = Detector(detection_parameters(), T0)
                events = self._segment(detector, self._settling_time(detector),
                                       (base, 30), heating=True)
                self.assertNotIn(Kind.DOOR_OPEN, [item.kind for item in events])
                # The retained BIC route needs a measured warm baseline before
                # the falling hinge; a line falling since origin has no turn.
                events += self._segment(detector, 80, (base, 30),
                                        (base - .03 * 80, 30), heating=True)
                self.assertEqual(detector.heating_since, T0)
                self.assertIn(Kind.DOOR_OPEN, [item.kind for item in events])

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
    """Declared form evidence; original windows, water and event ownership stay real."""

    def setUp(self):
        self.parameters = detection_parameters()
        self.p = self.parameters.values
        self.step = int(self.p["grid_seconds"])
        self.side = self.p["curve_original_samples"]
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
                    direction=None, onset_not_before=None, hinge_order=1):
        """Return declared local witnesses, retaining the real original support."""
        # The declared shapes describe first-order hinges only; they do not
        # supply evidence for the detector's optional curvature comparison.
        if hinge_order != 1 or not samples:
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

    def _original_forms(self, shapes, windows, *, expected_positions, humidity_minima=None):
        """Declare directions on the real, causally available original support.

        The production helper still combines the required positions. A missing
        window remains unknown; varying tails without a declared form cannot
        supply background evidence. No water pair or event state is fabricated.
        """
        strength = 2 * max(
            self.p[key] for key in (
                "curve_original_significance", "curve_original_open_contrast",
                "curve_original_new_infusion_contrast",
                "curve_original_continuation_contrast",
                "curve_original_background_slope", "curve_original_background_residual",
            )
        )
        declared = {}
        for name in expected_positions:
            if name not in windows:
                return None
            pair = windows[name]
            selected = shapes[Position(name)] if isinstance(shapes, dict) else shapes
            features = []
            for quantity, window in (
                (Quantity.TEMPERATURE, pair.temperature),
                (Quantity.HUMIDITY, pair.humidity),
            ):
                compatible = [shape for shape in selected if (
                    shape["quantity"] == quantity
                    and self._at(shape["onset"] + self.side * self.step)
                    <= window.newest_at <= self._at(shape["until"])
                    and (shape["kind"] != "decay" or window.newest_at
                         > self._at(shape["onset"] + self.side * self.step))
                )]
                current_constant = all(value == window.current[0][1]
                                       for _, value in window.current)
                if compatible:
                    shape = max(compatible, key=lambda item: item["onset"])
                    before, after = {
                        "rise": (0, 1), "decay": (1, -1),
                        "open": (0, -1), "close": (-1, 1),
                    }[shape["kind"]]
                    features.append(replace(
                        window, v=after * strength * window.e,
                        b=before * strength * window.e_minus, r=0.0,
                    ))
                else:
                    features.append(replace(
                        window, v=0.0, b=0.0,
                        r=0.0 if current_constant else strength,
                    ))
            declared[name] = OriginalPositionWindows(*features)
        return original_contrasts(
            declared, expected_positions=expected_positions, humidity_minima=humidity_minima,
        )

    @contextmanager
    def _patch_math(self, shapes, *, forms=None):
        def historical_onset(samples, **kwargs):
            selected = shapes
            if isinstance(shapes, dict):
                position = (Position.LOWER if samples and samples[0][1] <= self.peak / 2
                            else Position.UPPER)
                selected = shapes[position]
            return self._math_curve(selected, samples, **kwargs)

        # Legacy candidates only supply optional earlier onset anchors. The
        # episode must independently qualify through the new original forms.
        with patch(
            "custom_components.ha_sauna.core.detector.curve_change",
            side_effect=historical_onset,
        ), patch(
            "custom_components.ha_sauna.core.detector.original_contrasts",
            side_effect=forms or (lambda windows, **kwargs:
                                  self._original_forms(shapes, windows, **kwargs)),
        ):
            yield

    def _send(self, detector, second, humidity, events, *, temperature=None,
              measured_second=None, humidity_source=None, controller=None,
              detected_at=None, omit=()):
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
                if (position, quantity) in omit:
                    continue
                source = humidity_source if quantity == Quantity.HUMIDITY else None
                self.assertTrue(detector.accept(measurement(
                    position, quantity, value, second, source=source, measured=measured_at,
                )))

        def received(detection):
            if detection.kind == Kind.INFUSION:
                episode = detector.infusion_episode
                self.assertIsNotNone(episode)
                self.assertIs(episode.confirmed_detection, detection)
                self.assertEqual(episode.confirmed_at, detection.detected_at)
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
            detected_at=detected_at,
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
        with self._patch_math(shapes):
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
        with self._patch_math(shapes):
            self._feed(detector, self.begin + (self.side + 1) * self.step, points, events)
            self.assertEqual(detector._infusion_episode["phase"], "pending")
            old = detector.infusion_episode
            self._feed(detector, expired, points, events)
            self.assertIsNone(detector._infusion_episode)
            self.assertEqual(self._infusions(events), [])
            self.assertIsNone(detector._original_infusion_candidate)
            self._feed(detector, unrelated_fall, points, events)
            self.assertIsNone(detector._infusion_episode)
            self.assertEqual(self._infusions(events), [])
            first_rise = next_rise + self.side * self.step
            self._feed(detector, first_rise, points, events)
            rise = detector._original_infusion_candidate
            self.assertEqual(rise["onsets"][Position.UPPER], self._at(next_rise))
            self.assertFalse(rise["strong"])
            self.assertIsNone(detector._infusion_episode)
            self._feed(detector, first_rise + self.step, points, events)
            self.assertEqual(detector._infusion_episode["phase"], "pending")
            self.assertEqual(detector._infusion_episode["onset_at"], self._at(next_rise))
            self.assertNotEqual(detector.infusion_episode.bound_at, old.bound_at)
            first_decay = next_peak + self.side * self.step
            self._feed(detector, first_decay, points, events)
            self.assertFalse(detector._original_infusion_candidate["fall_confirmed"])
            self.assertEqual(self._infusions(events), [])
            self._feed(detector, next_fall, points, events)
        infusions = self._infusions(events)
        self.assertEqual(len(infusions), 1)
        self.assertEqual(infusions[0].effective_at, self._at(next_rise))
        self.assertGreater(infusions[0].trace_at, self._at(first_decay))

    def test_global_rise_without_supported_original_direction_cannot_start_a_pulse(self):
        points, shapes, _, _ = self._scenario(merged=False)
        for evidence in ("unknown", "level"):
            with self.subTest(original_direction=evidence):
                detector = Detector(self.parameters, T0)
                events = []

                def forms(windows, **kwargs):
                    contrasts = self._original_forms(shapes, windows, **kwargs)
                    if evidence != "level" or contrasts is None:
                        return None
                    level = {
                        name: replace(pair, humidity=replace(pair.humidity, v=0.0))
                        for name, pair in contrasts.positions
                    }
                    return original_contrasts(level, **kwargs)

                with self._patch_math(shapes, forms=forms):
                    self._feed(detector, self.begin + self.width, points, events)
                self.assertEqual(self._infusions(events), [])
                self.assertIsNone(detector._infusion_episode)

    def test_unknown_water_pair_is_not_negative_and_complete_negative_pairing_rejects(self):
        points, shapes, _, _ = self._scenario(merged=False)
        peak = self.begin + self.width
        fall = peak + self.width
        cold = trace(85)[0]
        temperatures = [(0, self.temperature), (self.begin, self.temperature), (peak, cold)]
        self.assertLess(absolute_humidity(cold, self.peak),
                        absolute_humidity(self.temperature, self.baseline))
        base_at = self._at(self.begin + self.step)
        pair_available = False

        def water_pair(humidity_sample, temperature_samples, **kwargs):
            if humidity_sample[0] == base_at and not pair_available:
                return None
            return original_water_pair(humidity_sample, temperature_samples, **kwargs)

        detector = Detector(self.parameters, T0, (Position.UPPER,))
        events = []
        with self._patch_math(shapes), patch(
            "custom_components.ha_sauna.core.detector.original_water_pair", side_effect=water_pair,
        ):
            self._feed(detector, fall, points, events, temperatures=temperatures)
            component = detector._original_infusion_candidate
            self.assertIsNotNone(component)
            self.assertTrue(component["strong"])
            self.assertTrue(component["support_end"])
            self.assertFalse(component["qualified"])
            self.assertEqual(component["bases"][Position.UPPER][0], base_at)
            self.assertFalse(component["water"][Position.UPPER]["complete"])
            self.assertIsNone(component["water"][Position.UPPER]["change"])
            self.assertIsNone(detector.infusion_episode)
            self.assertEqual(self._infusions(events), [])
            pair_available = True
            self._send(detector, fall + self.step, self.level, events, temperature=cold)
        witness = component["water"][Position.UPPER]
        self.assertTrue(witness["complete"])
        self.assertLessEqual(witness["change"], 0)
        self.assertIsNone(detector._original_infusion_candidate)
        self.assertIsNone(detector.infusion_episode)
        self.assertEqual(self._infusions(events), [])

    def test_original_gap_between_grid_ticks_restarts_background_support(self):
        count = int(BY_KEY["curve_original_samples"].minimum)
        self.parameters = detection_parameters(
            curve_original_samples=count,
            curve_original_gap_humidity_upper_seconds=self.step / 4,
        )
        self.p = self.parameters.values
        self.side = count
        self.width = (2 * count + 2) * self.step
        points, shapes, _, _ = self._scenario(merged=False)
        first_background = self.begin + 2 * self.width + count * self.step

        def forms(windows, **kwargs):
            result = self._original_forms(shapes, windows, **kwargs)
            if result is not None and min(
                min(pair.temperature.newest_at, pair.humidity.newest_at)
                for _, pair in result.positions
            ) < self._at(first_background):
                return replace(result, background_slope=2 * self.p["curve_original_background_slope"])
            return result

        detector = Detector(self.parameters, T0)
        events = []
        with self._patch_math(shapes, forms=forms):
            cadence = self.step / 4
            for tick in range(int(first_background / cadence) + 1):
                second = tick * cadence
                self._send(detector, second, self._value(points, second), events)
            original = self._infusions(events)[0]
            bound_at = detector.infusion_episode.bound_at
            reference = detector._infusion_episode["original_background"]
            self.assertIsNotNone(reference)
            self.assertEqual(detector.infusion_episode.phase, "active")
            # The withheld upper RH creates an unavailable prefix. Exactly 2K
            # fresh RH originals then make the final window valid again before
            # the next raster; that endpoint cannot bridge the hidden gap.
            self._send(
                detector, first_background + 3 * self.step / 8, self.level, events,
                omit=((Position.UPPER, Quantity.HUMIDITY),),
            )
            recovery_step = self.step / (2 * (2 * count - 1))
            for number in range(2 * count):
                second = first_background + self.step / 2 + number * recovery_step
                self._send(detector, second, self.level, events)
            endpoint = detector._original_window_cache[Position.UPPER, Quantity.HUMIDITY][1]
            self.assertIsNotNone(endpoint)
            self.assertEqual(endpoint.newest_at, self._at(first_background + self.step))
            self.assertEqual(detector.infusion_episode.phase, "active")
            self.assertIsNone(detector.infusion_episode.level_end_at)
            self.assertNotEqual(detector._infusion_episode["original_background"], reference)
            self._send(detector, first_background + self.step + recovery_step,
                       self.level, events)
            events.extend(detector.advance(self._at(first_background + 2 * self.step), enabled=True))
        self.assertEqual(detector.infusion_episode.phase, "ended")
        self.assertEqual(detector.infusion_episode.bound_at, bound_at)
        self.assertIs(detector.infusion_episode.confirmed_detection, original)
        self.assertEqual(self._infusions(events), [original])

    def test_original_sample_count_requires_capacity_for_both_windows(self):
        count = self.p["curve_original_samples"]
        accepted = Parameters({"curve_original_samples": count, "curve_max_points": 2 * count})
        self.assertEqual(accepted.values["curve_max_points"], 2 * count)
        with self.assertRaises(ParameterError) as invalid:
            Parameters({"curve_original_samples": count, "curve_max_points": 2 * count - 1})
        self.assertEqual(invalid.exception.key, "curve_max_points")

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
        with self._patch_math(shapes):
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
        self.assertIsNone(detector.infusion_episode)
        with self._patch_math(shapes):
            self._feed(detector, self.begin + (self.side + 1) * self.step, points, events)
            pending = detector.infusion_episode
            self.assertEqual(pending.phase, "pending")
            self.assertEqual(pending.positions, detector.positions)
            self.assertEqual(pending.onset_at, self._at(self.begin))
            self.assertIsNone(pending.confirmed_detection)
            self.assertIsNone(pending.confirmed_at)
            self.assertIsNone(pending.level_end_at)
            self.assertIsNone(pending.end_basis)
            self._feed(detector, rise_two, points, events)
            self.assertEqual(len(self._infusions(events)), 1)
            self.assertEqual(detector._infusion_episode["phase"], "active")
            original = self._infusions(events)[0]
            parameter_values = detector.p
            # Copy the immutable parameter view as values, retaining the full
            # deep copy for every mutable part of the detector's owner state.
            before_read = deepcopy(
                detector.__dict__, {id(parameter_values): dict(parameter_values)},
            )
            with patch.multiple(
                "custom_components.ha_sauna.core.detector",
                **dict.fromkeys(("curve_change", "curve_form", "curve_level",
                                 "curve_recovery", "curve_trend", "original_window",
                                 "original_contrasts", "original_thermal_contrasts",
                                 "original_water_pair"), DEFAULT),
            ) as calculations:
                active = detector.infusion_episode
                self.assertEqual(active, detector.infusion_episode)
                with self.assertRaises(FrozenInstanceError):
                    active.phase = "ended"
            for calculation in calculations.values():
                calculation.assert_not_called()
            self.assertIs(detector.p, parameter_values)
            self.assertEqual(detector.__dict__, before_read)
            self.assertEqual(active.phase, "active")
            self.assertEqual(active.bound_at, pending.bound_at)
            self.assertIs(active.confirmed_detection, original)
            self.assertEqual(active.onset_at, original.effective_at)
            self.assertEqual(active.confirmed_at, original.detected_at)
            self.assertIsNone(active.level_end_at)
            self._feed(
                detector, fall_two + (self.side + 1) * self.step,
                points, events,
            )
        self.assertEqual(len(self._infusions(events)), 1)
        self.assertEqual(detector._infusion_episode["phase"], "ended")
        self.assertEqual(detector._infusion_episode["end_basis"], "original_background")
        ended = detector.infusion_episode
        self.assertEqual(ended.phase, "ended")
        self.assertEqual(ended.bound_at, pending.bound_at)
        self.assertIs(ended.confirmed_detection, original)
        self.assertEqual(ended.level_end_at,
                         self._at(fall_two + self.side * self.step))
        self.assertEqual(ended.end_basis, "original_background")
        self.assertEqual(ended.last_rise_at,
                         tuple((position, self._at(rise_two)) for position in detector.positions))
        for snapshot, phase in ((pending, "pending"), (active, "active")):
            self.assertEqual(snapshot.phase, phase)
            self.assertIsNone(snapshot.level_end_at)
            self.assertEqual(snapshot.last_rise_at,
                             tuple((position, self._at(self.begin))
                                   for position in detector.positions))
        self.assertIsNone(pending.confirmed_detection)
        self.assertIsNone(pending.confirmed_at)

    def test_qualified_continuation_keeps_delayed_fall_beyond_historical_onset_window(self):
        points, shapes, rise_two, _ = self._scenario(merged=True)
        historical_onset = rise_two
        second_peak = (
            historical_onset + self.p["curve_window_seconds"]
            - (self.side - 2) * self.step
        )
        second_fall = second_peak + self.width
        points[-2:] = [(second_peak, self.peak), (second_fall, self.level)]
        shapes[2:] = [self._shape("decay", second_peak, second_fall)]
        reveal_at = rise_two + (self.side + 2) * self.step
        expired_at = historical_onset + self.p["curve_window_seconds"] + self.step
        fall_at = second_peak + self.side * self.step
        self.assertLess(reveal_at + self.step, expired_at)
        self.assertLess(expired_at, fall_at)
        detector = Detector(self.parameters, T0)
        events = []
        # Only the optional historical onset is declared. All original forms,
        # water pairs and fresh fall/background witnesses use production math.
        with self._patch_math(shapes, forms=original_contrasts):
            self._feed(detector, rise_two, points, events)
            self.assertEqual(len(self._infusions(events)), 1)
            original = self._infusions(events)[0]
            active = detector.infusion_episode
            self.assertEqual(active.phase, "active")
            first_component = detector._infusion_episode["original_component"]
            self._feed(detector, reveal_at, points, events)
            component = detector._original_infusion_candidate
            self.assertIsNot(component, first_component)
            self.assertIs(detector._infusion_episode["original_component"], component)
            self.assertTrue(component["qualified"])
            self.assertTrue(component["continuing"])
            self.assertFalse(component["fall_confirmed"])
            self.assertTrue(all(witness["change"] > 0
                                for witness in component["water"].values()))
            native_at = component["received_at"]
            self.assertGreater(native_at, self._at(historical_onset))
            self.assertEqual(set(component["onsets"].values()), {native_at})
            # The second rise qualifies without an optional historical model.
            # Only a later original exposes that earlier, existing onset.
            shapes.insert(2, self._shape("rise", historical_onset, second_peak))
            self._feed(detector, reveal_at + self.step, points, events)
            self.assertIs(detector._original_infusion_candidate, component)
            self.assertEqual(component["received_at"], native_at)
            self.assertEqual(set(component["onsets"].values()),
                             {self._at(historical_onset)})
            for position in detector.positions:
                self.assertIn(self._at(historical_onset), {
                    stamp for stamp, _ in
                    detector._curve_series[position, Quantity.HUMIDITY]["points"]
                })
            self.assertLess(self._at(fall_at), native_at + timedelta(
                seconds=self.p["curve_window_seconds"],
            ))
            self._feed(detector, expired_at, points, events)
            self.assertIs(detector._original_infusion_candidate, component)
            self.assertEqual(set(component["onsets"].values()),
                             {self._at(historical_onset)})
            self.assertTrue(component["qualified"])
            self.assertFalse(component["fall_confirmed"])
            self.assertEqual(detector.infusion_episode.phase, "active")
            self.assertEqual(detector.infusion_episode.bound_at, active.bound_at)
            self.assertIs(detector.infusion_episode.confirmed_detection, original)
            self.assertEqual(self._infusions(events), [original])
            self._feed(detector, fall_at, points, events)
            self.assertIs(detector._original_infusion_candidate, component)
            self.assertTrue(component["fall_confirmed"])
            for _, pair in component["fall_windows"]:
                self.assertGreater(pair.humidity.current[0][0], self._at(second_peak))
            self._feed(detector, second_fall + (self.side + 1) * self.step, points, events)
        ended = detector.infusion_episode
        self.assertEqual(ended.phase, "ended")
        self.assertEqual(ended.end_basis, "original_background")
        self.assertGreater(ended.level_end_at, self._at(fall_at))
        self.assertEqual(ended.bound_at, active.bound_at)
        self.assertIs(ended.confirmed_detection, original)
        self.assertEqual(self._infusions(events), [original])
        self.assertEqual(active.phase, "active")
        self.assertIsNone(active.level_end_at)

    def test_upper_peak_preserves_lower_tail_until_shared_level_and_next_infusion(self):
        first_peak = self.begin + self.width
        first_fall = first_peak + self.width
        upper_rise = first_fall + max(1, self.side - 2) * self.step
        upper_peak = upper_rise + self.width
        upper_fall = upper_peak + self.width
        settled = upper_fall + (self.side + 1) * self.step
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

        position_shapes = {Position.UPPER: upper_shapes, Position.LOWER: common_shapes}

        for paused in (False, True):
            with self.subTest(paused=paused):
                detector = Detector(self.parameters, T0)
                controller = Controller(self.parameters)
                controller.set_temperature(self.temperature, T0)
                controller.begin_session("s", T0)
                controller.process(event("initial-open", Kind.DOOR_OPEN, 0))
                controller.process(event("initial-close", Kind.DOOR_CLOSE, 0))
                events = []
                with self._patch_math(position_shapes):
                    self._feed(detector, first_fall - self.step, points, events,
                               controller=controller)
                    self.assertEqual(len(self._infusions(events)), 1)
                    self.assertEqual(detector._infusion_episode["phase"], "active")
                    first_episode = detector.infusion_episode
                    first_component = detector._infusion_episode["original_component"]
                    lower_decay = dict(first_component["fall_windows"])["lower"].humidity
                    self.assertEqual(first_component["peaks"][Position.LOWER][0],
                                     self._at(first_peak))
                    self.assertTrue(all(value < self.peak / 2
                                        for _, value in lower_decay.current))
                    first_gang = controller.session.timeline.active
                    self.assertIsNotNone(first_gang)
                    self.assertEqual(len(first_gang.infusion_events), 1)
                    if paused:
                        # A global gap interrupts the shared background proof.
                        # The lower position can recover without a new peak.
                        self._send(detector, first_fall, None, events, controller=controller)
                        interrupted = detector.infusion_episode
                        self.assertEqual(interrupted.interrupted_positions, detector.positions)
                        self.assertEqual(interrupted.bound_at, first_episode.bound_at)
                        self.assertEqual(first_episode.interrupted_positions, ())
                    self._feed(detector, upper_peak, points, events, controller=controller)
                    episode = detector._infusion_episode
                    self.assertEqual(episode["phase"], "active")
                    self.assertIsNone(episode["level_end_at"])
                    self.assertIsNone(episode["original_background"])
                    self.assertEqual(len(self._infusions(events)), 1)
                    self.assertEqual(episode["onset_at"], self._at(self.begin))
                    if not paused:
                        preserved = dict(episode["original_component"]["fall_windows"])["lower"]
                        self.assertEqual(preserved.humidity, lower_decay)
                    lower_level = detector._original_window_cache[
                        Position.LOWER, Quantity.HUMIDITY
                    ][1]
                    self.assertTrue(all(value == self.level / 2
                                        for _, value in lower_level.current))
                    self.assertEqual(len(controller.session.timeline.active.infusion_events), 1)
                    self.assertEqual(len([item for item in controller.consumer_events
                                          if item.kind == "infusion"]), 1)
                    # The upper tail must also settle; a fresh shared original
                    # background is required before the episode can end.
                    self._feed(detector, settled, points, events, controller=controller)
                    episode = detector._infusion_episode
                    self.assertEqual(episode["phase"], "ended")
                    self.assertEqual(episode["end_basis"], "original_background")
                    self.assertGreater(episode["level_end_at"], self._at(upper_peak))
                    end_windows = dict(episode["original_end_windows"])
                    self.assertEqual(tuple(end_windows), ("upper", "lower"))
                    for name, pair in end_windows.items():
                        expected = self.level if name == "upper" else self.level / 2
                        self.assertTrue(all(value == expected for _, value in pair.humidity.current))
                        self.assertGreater(pair.humidity.newest_at, self._at(upper_peak))
                    self.assertEqual(len(self._infusions(events)), 1)
                    ended_episode = detector.infusion_episode
                    self._feed(detector, next_fall, points, events, controller=controller)
                infusions = self._infusions(events)
                self.assertEqual(len(infusions), 2)
                self.assertEqual([item.effective_at for item in infusions],
                                 [self._at(self.begin), self._at(next_rise)])
                self.assertTrue(all(item.channels == ("upper", "lower") for item in infusions))
                self.assertTrue(all(item.effective_at < item.trace_at for item in infusions))
                next_episode = detector.infusion_episode
                self.assertNotEqual(next_episode.bound_at, ended_episode.bound_at)
                self.assertIs(next_episode.confirmed_detection, infusions[1])
                self.assertEqual(ended_episode.bound_at, first_episode.bound_at)
                self.assertIs(ended_episode.confirmed_detection, infusions[0])
                self.assertEqual(ended_episode.phase, "ended")
                self.assertEqual(first_episode.phase, "active")
                self.assertEqual(first_episode.interrupted_positions, ())
                if paused:
                    self.assertEqual(interrupted.interrupted_positions, detector.positions)
                gang = controller.session.timeline.active
                self.assertIsNotNone(gang)
                self.assertEqual(gang.gang_id, first_gang.gang_id)
                # Only completed confirmed gangs contribute to gang_count.
                self.assertEqual(controller.session.timeline.gang_count, 0)
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
        with self._patch_math(shapes):
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
        # The first new original follows the same-stamp corrections and one pure tick;
        # their elapsed time must not introduce a separate source-gap failure.
        confirmed = first_decay + 5 * self.step
        for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY):
            self.assertLessEqual(
                confirmed - first_decay,
                self.p[f"curve_original_gap_{quantity.value}_upper_seconds"],
            )
        with self._patch_math(shapes):
            self._feed(detector, first, points, events)
            self.assertEqual(self._infusions(events), [])
            self.assertIsNone(detector.infusion_episode)
            status = detector._original_infusion_candidate
            self.assertEqual(status["received_at"], self._at(first))
            self.assertFalse(status["strong"])
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
            pending = detector.infusion_episode
            self.assertEqual(pending.bound_at, self._at(first + 4 * self.step))
            self.assertIsNone(pending.confirmed_detection)
            self._feed(detector, first_decay, points, events)
            self.assertFalse(detector._original_infusion_candidate["fall_confirmed"])
            self.assertNotIn("fall_windows", detector._original_infusion_candidate)
            events.extend(detector.advance(self._at(first_decay + self.step), enabled=True))
            self._send(detector, first_decay + 2 * self.step,
                       self._value(points, first_decay), events, measured_second=first_decay)
            self._send(detector, first_decay + 3 * self.step,
                       self._value(points, first_decay) - .1, events,
                       measured_second=first_decay)
            self.assertEqual(detector._infusion_episode["phase"], "pending")
            self.assertEqual(self._infusions(events), [])
            unconfirmed = detector.infusion_episode
            self.assertEqual(unconfirmed.bound_at, pending.bound_at)
            self.assertIsNone(unconfirmed.confirmed_detection)
            self.assertIsNone(unconfirmed.confirmed_at)
            events.extend(detector.advance(self._at(confirmed - self.step), enabled=True))
            self.assertEqual(self._infusions(events), [])
            self._send(detector, confirmed, self._value(points, confirmed), events)
        self.assertEqual(len(self._infusions(events)), 1)
        infusion = self._infusions(events)[0]
        self.assertEqual(infusion.effective_at, self._at(self.begin))
        self.assertEqual(infusion.trace_at, self._at(confirmed))
        self.assertEqual(infusion.detected_at, infusion.trace_at)
        self.assertEqual(detector._infusion_episode["phase"], "active")
        active = detector.infusion_episode
        self.assertEqual(active.bound_at, pending.bound_at)
        self.assertIs(active.confirmed_detection, infusion)
        self.assertEqual(active.confirmed_at, self._at(confirmed))
        self.assertEqual(pending.phase, "pending")
        self.assertIsNone(pending.confirmed_detection)
        self.assertLessEqual(
            detector.diagnostic["metrics"]["upper"]["infusion_absolute_humidity_delta"], 0,
        )

    def test_level_end_needs_new_original_not_ticks_or_same_time_correction(self):
        points, shapes, _, _ = self._scenario(merged=False)
        detector = Detector(self.parameters, T0, (Position.UPPER,))
        events = []
        first = self.begin + 2 * self.width + (self.side - 1) * self.step
        invalid_change = 2 * self.side * self.p["curve_original_noise_humidity_upper"] * max(
            self.p["curve_original_background_slope"],
            self.p["curve_original_background_residual"],
        )
        with self._patch_math(shapes):
            self._feed(detector, first, points, events)
            witness = detector._infusion_episode["original_background"]
            self.assertEqual(witness, (("upper", self._at(first), self._at(first)),))
            self.assertIsNone(detector._infusion_episode["level_end_at"])
            events.extend(detector.advance(self._at(first + self.step), enabled=True))
            self._send(detector, first + 2 * self.step, self.level - invalid_change, events,
                       measured_second=first)
            self._send(detector, first + 3 * self.step, self.level, events,
                       measured_second=first)
            self.assertEqual(detector._infusion_episode["phase"], "active")
            self.assertIsNone(detector._infusion_episode["original_background"])
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
        with self._patch_math(shapes):
            self._feed(detector, gap - self.step, points, events)
            self._send(detector, gap, None, events)
            self.assertEqual(detector._infusion_episode["phase"], "active")
            self.assertTrue(detector._infusion_episode["interrupted"])
            self._feed(detector, next_rise - self.step, points, events,
                       humidity_source="sensor.recovered_humidity")
            self.assertEqual(detector._infusion_episode["phase"], "ended")
            self.assertEqual(detector._infusion_episode["end_basis"], "original_background")
            self.assertEqual(detector._infusion_episode["decays"], {})
            self.assertTrue(detector.infusion_episode.interrupted_positions)
            self.assertTrue(all(
                value == self.level for _, pair in
                detector._infusion_episode["original_end_windows"]
                for _, value in pair.humidity.current
            ))
            self.assertEqual(len(self._infusions(events)), 1)
            self._feed(detector, next_fall, points, events,
                       humidity_source="sensor.recovered_humidity")
        self.assertEqual(len(self._infusions(events)), 2)
        self.assertTrue(any(
            row["decisions"]["infusion_background"]["result"] is True
            for row in observed
        ))

    def test_ols_opening_uses_only_an_eligible_thermal_onset_without_backdating_confirmation(self):
        flank_seconds = self.p["curve_min_side_samples"] * self.step
        ready = self.begin + flank_seconds
        confirmed = ready + self.step
        recognized_at = self._at(confirmed + 5 * self.step)
        end = self.begin + self.width
        initial_t, initial_h = trace(70)
        falling_t, falling_h = trace(70 + self.step)
        # Spread the existing fixture's first loss over the configured local
        # model flank, leaving an observable onset before OLS confirmation.
        temperatures = [(0, self.temperature), (self.begin, self.temperature),
                        (end, self.temperature
                         + (falling_t - initial_t) * self.width / flank_seconds)]
        humidities = [(0, self.baseline), (self.begin, self.baseline),
                      (end, self.baseline
                       + (falling_h - initial_h) * self.width / flank_seconds)]
        shapes = [
            self._shape("open", self.begin, end, Quantity.TEMPERATURE),
            self._shape("open", self.begin, end),
        ]
        prepared = Detector(self.parameters, T0)
        prepared.report_heating(True, T0)
        positions = prepared.positions
        quantities = (Quantity.TEMPERATURE, Quantity.HUMIDITY)
        mixed_key = ("door_open", positions, quantities)
        thermal_key = ("door_open_heating", positions, (Quantity.TEMPERATURE,))
        events = []
        # The declared local models supply onset attribution only. OLS sees
        # the actual small falls, which have not confirmed the opening yet.
        with self._patch_math(shapes, forms=original_contrasts):
            self._feed(prepared, ready, humidities, events, temperatures=temperatures)
            self.assertFalse(prepared.open)
            self.assertNotIn(Kind.DOOR_OPEN, [item.kind for item in events])
            self.assertIn(thermal_key, prepared._curve_proposals)
            self.assertTrue(prepared._remember_curve_proposal(
                "door_open", positions, quantities, floor=prepared._curve_closed_since,
                context=(prepared._curve_door_context, prepared._curve_closed_since),
                observed_at=self._at(ready),
            ))
            self.assertIn(mixed_key, prepared._curve_proposals)
            # Reuse this single prefix for the routing guards. The next RH
            # original has no local RH model and no new T confirmation.
            shapes.pop()
            for case in ("complete_thermal", "incomplete_thermal",
                         "ambiguous_mixed", "controller_rejected_mixed"):
                with self.subTest(case=case):
                    detector = deepcopy(prepared, {id(prepared.p): prepared.p})
                    if case == "ambiguous_mixed":
                        detector._curve_proposals[mixed_key] = replace(
                            detector._curve_proposals[mixed_key], ambiguous=True,
                        )
                        detector._curve_proposal_status[mixed_key].update(
                            status="ambiguous", reason="competing_complete_proposal",
                        )
                    elif case == "controller_rejected_mixed":
                        self.assertEqual(detector._curve_beginning(
                            "door_open", positions, quantities, self._at(ready),
                            floor=detector._curve_closed_since, kind=Kind.DOOR_OPEN,
                            now=self._at(ready), decision_at=self._at(ready),
                            onset_allowed=lambda *_: False,
                        ), self._at(ready))
                        self.assertEqual(
                            detector._curve_proposal_status[mixed_key]["reason"],
                            "controller_context",
                        )
                    else:
                        detector._curve_proposals.pop(mixed_key)
                        detector._curve_proposal_status.pop(mixed_key)
                    for position in positions:
                        detector._curve_candidates.pop(("door_open", position, Quantity.HUMIDITY))
                    if case == "incomplete_thermal":
                        detector._curve_candidates.pop((
                            "door_open_heating", positions[-1], Quantity.TEMPERATURE,
                        ))
                        detector._curve_proposals.pop(thermal_key)
                    found = []
                    self._send(
                        detector, confirmed, self._value(humidities, confirmed), found,
                        omit=tuple((position, Quantity.TEMPERATURE) for position in positions),
                        detected_at=recognized_at,
                    )
                    openings = [item for item in found if item.kind == Kind.DOOR_OPEN]
                    self.assertEqual(len(openings), 1)
                    opening = openings[0]
                    uses_thermal = case == "complete_thermal"
                    effective_at = self._at(self.begin if uses_thermal else confirmed)
                    self.assertEqual(opening.effective_at, effective_at)
                    self.assertEqual(opening.trace_at, self._at(confirmed))
                    self.assertEqual(opening.detected_at, recognized_at)
                    self.assertEqual(opening.channels, tuple(position.value for position in positions))
                    self.assertTrue(detector.open)
                    self.assertTrue(detector.door_episode["opening_humidity"])
                    self.assertEqual(detector.opened_at, effective_at)
                    self.assertEqual(detector.door_episode["started_at"], effective_at)
                    diagnostic = detector.diagnostic
                    self.assertTrue(diagnostic["decisions"]["door_open"]["result"])
                    self.assertFalse(diagnostic["decisions"]["door_open_heating"]["result"])
                    self.assertEqual(diagnostic["door_open_onset_attribution"], {
                        "confirmation_basis": "original_ols",
                        "route": "door_open_heating" if uses_thermal else "door_open",
                        "quantities": (("temperature",) if uses_thermal else
                                       ("temperature", "humidity")),
                        "thermal_fallback": uses_thermal,
                        "used_proposal": uses_thermal,
                        "effective_at": effective_at,
                    })
                    if uses_thermal:
                        proposal = detector._curve_proposal_status[thermal_key]
                        self.assertEqual(proposal["confirmed_at"], recognized_at)
                        self.assertEqual(proposal["consumed_onset_at"], self._at(self.begin))

    def test_door_context_assigns_a_new_infusion_to_the_next_real_gang(self):
        first_peak = self.begin + self.width
        first_fall = first_peak + self.width
        door_start = first_fall + max(1, self.side - 2) * self.step
        door_bottom = door_start + self.width
        close_start = door_bottom + max(
            self.p["infusion_window_seconds"], self.p["median_seconds"],
        ) + self.side * self.step
        close_end = close_start + self.width
        next_rise = close_end + self.p["infusion_window_seconds"] + self.p["median_seconds"]
        next_peak = next_rise + self.width
        next_fall = next_peak + self.width
        # Reuse the same physical opening as the detector's water-form tests.
        fixture = DetectorTests()
        _, fixture_plateau, _, fixture_values = fixture._ventilation_curve(
            Detector(self.parameters, T0),
        )
        initial_t, initial_h = fixture_values(0)
        fallen_t, fallen_h = fixture_values(fixture_plateau)
        cold = self.temperature + fallen_t - initial_t
        dry = self.level + fallen_h - initial_h
        close_temperature = cold + (cold - self.temperature) * (close_start - door_bottom) / self.width
        plateau_water = absolute_humidity(cold, dry)
        close_humidity = fixture._humidity_for_water(close_temperature, plateau_water)
        points = [
            (0, self.baseline), (self.begin, self.baseline), (first_peak, self.peak),
            (first_fall, self.level), (door_start, self.level),
            (door_bottom, dry), (close_start, close_humidity),
            (close_end, self.level), (next_rise, self.level), (next_peak, self.peak),
            (next_fall, self.level),
        ]
        temperatures = [(0, self.temperature), (door_start, self.temperature),
                        (door_bottom, cold), (close_start, close_temperature),
                        (close_end, self.temperature)]
        points = [(second, fixture._humidity_for_water(
            self._value(temperatures, second), plateau_water,
        ) if door_bottom <= second <= close_start else self._value(points, second))
            for second in range(0, next_fall + self.step, self.step)]
        shapes = [self._shape("rise", self.begin, first_peak),
                  self._shape("decay", first_peak, first_fall),
                  self._shape("rise", next_rise, next_peak),
                  self._shape("decay", next_peak, next_fall)]
        for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY):
            shapes.extend((self._shape("open", door_start, door_bottom, quantity),
                           self._shape("close", close_start, close_end, quantity)))
        episodes_at_opening = []

        def observe(row):
            if Kind.DOOR_OPEN in row["signals"]:
                episodes_at_opening.append(detector.infusion_episode)

        detector = Detector(self.parameters, T0, observer=observe)
        controller = Controller(self.parameters)
        controller.set_temperature(self.temperature, T0)
        controller.begin_session("s", T0)
        controller.process(event("initial-open", Kind.DOOR_OPEN, 0))
        controller.process(event("initial-close", Kind.DOOR_CLOSE, 0))
        events = []
        with self._patch_math(shapes):
            self._feed(detector, door_start - self.step, points, events,
                       temperatures=temperatures, controller=controller)
            self.assertEqual(detector._infusion_episode["phase"], "active")
            self.assertEqual(len(controller.session.timeline.active.infusion_events), 1)
            first_episode = detector.infusion_episode
            self._feed(detector, next_fall, points, events,
                       temperatures=temperatures, controller=controller)
        self.assertEqual(len(self._infusions(events)), 2)
        self.assertEqual(episodes_at_opening, [None])
        self.assertEqual(first_episode.phase, "active")
        self.assertIsNone(first_episode.level_end_at)
        next_episode = detector.infusion_episode
        self.assertNotEqual(next_episode.bound_at, first_episode.bound_at)
        self.assertIs(first_episode.confirmed_detection, self._infusions(events)[0])
        self.assertIs(next_episode.confirmed_detection, self._infusions(events)[1])
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
        ended = self.begin + 2 * self.width + self.side * self.step
        next_rise = ended + 3 * self.step
        next_peak = next_rise + self.width
        shapes = shapes[:2] + [self._shape("rise", next_rise, next_peak)]
        detector = Detector(self.parameters, T0, (Position.UPPER,))
        events = []
        invalid_change = 2 * self.side * self.p["curve_original_noise_humidity_upper"] * max(
            self.p["curve_original_background_slope"],
            self.p["curve_original_background_residual"],
        )
        with self._patch_math(shapes):
            self._feed(detector, ended, points, events)
            self.assertEqual(detector._infusion_episode["phase"], "ended")
            ended_episode = detector.infusion_episode
            self.assertEqual(ended_episode.level_end_at, self._at(ended))
            original = self._infusions(events)[0]
            self.assertIs(ended_episode.confirmed_detection, original)
            self._send(detector, ended + self.step, self.level - invalid_change, events,
                       measured_second=ended)
            self.assertEqual(detector._infusion_episode["phase"], "active")
            self.assertIsNone(detector._infusion_episode["level_end_at"])
            reopened = detector.infusion_episode
            self.assertEqual(reopened.bound_at, ended_episode.bound_at)
            self.assertIs(reopened.confirmed_detection, original)
            self.assertIsNone(reopened.level_end_at)
            self.assertIsNone(reopened.end_basis)
            continued = [(0, self.level - invalid_change), (next_rise, self.level - invalid_change),
                         (next_peak, self.peak)]
            self._feed(detector, next_peak, continued, events)
        self.assertEqual(len(self._infusions(events)), 1)
        self.assertEqual(detector._infusion_episode["phase"], "active")
        self.assertEqual(detector.infusion_episode.bound_at, ended_episode.bound_at)
        self.assertIs(detector.infusion_episode.confirmed_detection, original)
        self.assertEqual(ended_episode.phase, "ended")
        self.assertEqual(ended_episode.level_end_at, self._at(ended))
        self.assertEqual(ended_episode.end_basis, "original_background")

    def test_late_rise_before_level_end_reopens_the_original_infusion(self):
        points, shapes, _, _ = self._scenario(merged=False)
        first_peak = self.begin + self.width
        confirmed = first_peak + (self.side + 1) * self.step
        received_at = self._at(confirmed + self.step)
        ended = self.begin + 2 * self.width + self.side * self.step
        late_onset = ended - self.step
        second_peak = shapes[2]["until"]
        shapes = shapes[:2]
        detector = Detector(self.parameters, T0, (Position.UPPER,))
        events = []
        with self._patch_math(shapes):
            self._feed(detector, confirmed - self.step, points, events)
            self.assertEqual(self._infusions(events), [])
            self._send(detector, confirmed, self._value(points, confirmed), events,
                       detected_at=received_at)
            original = self._infusions(events)[0]
            self.assertEqual(original.trace_at, self._at(confirmed))
            self.assertEqual(original.detected_at, received_at)
            self._feed(detector, ended, points, events)
            ended_episode = detector.infusion_episode
            self.assertEqual(ended_episode.phase, "ended")
            self.assertEqual(ended_episode.level_end_at, self._at(ended))
            self.assertEqual(ended_episode.confirmed_at, received_at)
            self.assertIs(ended_episode.confirmed_detection, original)
            self.assertLess(self._at(late_onset), ended_episode.level_end_at)
            self.assertIn(self._at(late_onset), {
                stamp for stamp, _ in
                detector._curve_series[Position.UPPER, Quantity.HUMIDITY]["points"]
            })
            # Only the declared math witness arrives late. All further inputs
            # are new originals; no correction invalidates the former level.
            shapes.append(self._shape("rise", late_onset, second_peak))
            self._feed(detector, second_peak, points, events)
        continued = detector.infusion_episode
        self.assertEqual(continued.phase, "active")
        self.assertEqual(continued.bound_at, ended_episode.bound_at)
        self.assertIs(continued.confirmed_detection, original)
        self.assertEqual(continued.confirmed_at, received_at)
        self.assertEqual(continued.last_rise_at, ((Position.UPPER, self._at(late_onset)),))
        self.assertEqual(detector._infusion_episode["original_component"]["threshold"],
                         self.p["curve_original_continuation_contrast"])
        self.assertIsNone(continued.level_end_at)
        self.assertIsNone(continued.end_basis)
        self.assertEqual(self._infusions(events), [original])
        self.assertEqual(ended_episode.phase, "ended")
        self.assertEqual(ended_episode.level_end_at, self._at(ended))
        self.assertEqual(ended_episode.end_basis, "original_background")
