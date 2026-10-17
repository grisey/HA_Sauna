"""Synthetische Zeitreihen prüfen Kausalität, Ausfall und Quellenwechsel."""
import asyncio
import json
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
    original_decline_evidence,
    original_followup,
    original_thermal_contrasts,
    original_water_pair,
    original_window,
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


def sample(detector, second, temperature, humidity, positions=(Position.UPPER, Position.LOWER),
           *, humidity_cadence=None):
    for pos in positions:
        detector.accept(measurement(pos, Quantity.TEMPERATURE, temperature, second))
        if humidity_cadence is None or second % humidity_cadence == 0:
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


def opening_diagnostic(detector, row):
    """Freeze native references at their actual observation for failure reports."""
    candidate = (detector.door_episode or {}).get("original_open") or {}
    return deepcopy({
        "at": row["at"], "signals": row.get("signals", ()),
        "original_forms": row.get("original_forms"),
        "opening_proposals": row.get("curve_proposals", {}).get("door_open"),
        "onset_witnesses": candidate.get("onset_witnesses", ()),
        "local_temperature_references": {
            name: {"previous": window.previous, "current": window.current,
                   "previous_mean": window.mean_minus, "current_mean": window.mean,
                   "newest_at": window.newest_at}
            for name, window in candidate.get("temperature_fall_witnesses", {}).items()
        },
    })


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
        previous_binding = None
        for row in traces:
            native = row.get("original_forms", {})
            binding = native.get("candidate_received_at")
            newly_bound = binding is not None and binding != previous_binding
            previous_binding = binding
            if not newly_bound and not any(
                kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE) for kind in row["signals"]
            ):
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
                "original_forms": native,
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
            if detection.kind in (Kind.VENTILATION, Kind.VENTILATION_COMPLETE):
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
        complete = [e for e in found if e.kind == Kind.VENTILATION_COMPLETE]
        self.assertEqual(len(ventilation), 1)
        self.assertEqual(len(complete), 1)
        self.assertLess(ventilation[0].trace_at, T0 + timedelta(seconds=plateau))
        self.assertGreater(complete[0].trace_at, T0 + timedelta(seconds=plateau))
        self.assertLess(ventilation[0].trace_at, complete[0].trace_at)
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
        self.assertEqual(sum(item.kind == Kind.VENTILATION_COMPLETE
                             for item in statistics.session.timeline.processed), 1)
        self.assertIsNone(statistics.session.timeline.active)
        self.assertEqual(statistics.session.timeline.gang_count, 0)
        self.assertEqual(statistics.session.timeline.completed, ())
        self.assertIsNone(statistics.session.after_run)
        self.assertEqual(statistics.session.after_run_history, ())
        self.assertEqual(len(statistics_heat), 2)
        self.assertTrue(all(before == after for before, after in statistics_heat))
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


    def test_small_coherent_door_shapes_do_not_repeat_during_plateau(self):
        for thermal in (False, True):
            for positions in ((Position.UPPER,), (Position.UPPER, Position.LOWER)):
                with self.subTest(thermal=thermal, positions=positions):
                    traces = []
                    detector = Detector(detection_parameters(), T0, positions,
                                        observer=traces.append)
                    cadence = self._thermal_humidity_cadence(detector) if thermal else None
                    events = []
                    for second in range(130):
                        detector.report_heating(thermal, T0 + timedelta(seconds=second))
                        # Both routes receive an exact small form with turns at
                        # 30/50/70; its amplitude does not make it mere noise.
                        fall = max(0, min(second - 30, 70 - second, 20))
                        events += sample(
                            detector, second, 80 - .04 * fall,
                            30 if thermal else 30 - .08 * fall, positions, humidity_cadence=cadence,
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
                                         30 if thermal else 30 - .1 * fall, positions, humidity_cadence=cadence)
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

    def test_clear_upper_opening_requires_only_its_own_temperature_and_humidity(self):
        for lower_falls in (True, False):
            with self.subTest(lower_falls=lower_falls):
                traces = []
                detector = Detector(detection_parameters(), T0, observer=traces.append)
                events, native_openings = [], []
                lower_rate = .03 if lower_falls else 0
                for second in range(90):
                    detector.report_heating(True, T0 + timedelta(seconds=second))
                    fall = min(20, max(0, second - 30))
                    for position, rate in ((Position.UPPER, .12), (Position.LOWER, lower_rate)):
                        detector.accept(measurement(position, Quantity.TEMPERATURE,
                                                    80 - rate * fall, second))
                        detector.accept(measurement(position, Quantity.HUMIDITY,
                                                    30 - .08 * fall, second))
                    found = detector.advance(T0 + timedelta(seconds=second), enabled=True)
                    events += found
                    if any(item.kind == Kind.DOOR_OPEN for item in found):
                        native_openings.append(detector.door_episode["original_open"])
                diagnostic = self._door_failure_message(
                    events, traces, input_turns_s=(30, 50), temperature_rates=(.12, lower_rate),
                )
                openings = [item for item in events if item.kind == Kind.DOOR_OPEN]
                self.assertEqual(len(openings), 1, msg=diagnostic)
                opening = openings[0]
                self.assertEqual(opening.channels, ("upper",), msg=diagnostic)
                resolution = max(detector.grid_seconds, ceil(
                    detector.p["curve_window_seconds"]
                    / max(1, detector.curve_policy.max_candidates - 1)
                ))
                self.assertLessEqual(
                    abs((opening.effective_at - T0).total_seconds() - 30), resolution,
                    msg=diagnostic,
                )
                self.assertEqual(len(native_openings), 1, msg=diagnostic)
                native = native_openings[0]
                self.assertEqual(native["confirmation_received_at"], opening.trace_at,
                                 msg=diagnostic)
                self.assertLess(native["received_at"], native["confirmation_received_at"],
                                msg=diagnostic)
                self.assertLessEqual(opening.effective_at, opening.trace_at, msg=diagnostic)
                self.assertEqual(opening.trace_at, opening.detected_at, msg=diagnostic)
                row = next(row for row in traces if Kind.DOOR_OPEN in row["signals"])
                self.assertEqual(row["door_open_onset_attribution"]["confirmation_basis"],
                                 "original_ols", msg=diagnostic)
                self.assertTrue(row["original_forms"]["complete_fresh_original_block"]["result"],
                                msg=diagnostic)
                expected_positions = ("upper",)
                bound = original_contrasts(dict(native["windows"]),
                                           expected_positions=expected_positions)
                confirmed = original_contrasts(dict(native["confirmation_windows"]),
                                               expected_positions=expected_positions)
                self.assertGreater(bound.humidity_fall_onset,
                                   detector.p["curve_original_significance"], msg=diagnostic)
                self.assertGreater(confirmed.opening_support,
                                   detector.p["curve_original_open_contrast"], msg=diagnostic)
                self.assertEqual(tuple(name for name, _ in native["windows"]), expected_positions,
                                 msg=diagnostic)
                for name, binding in native["windows"]:
                    confirmation = dict(native["confirmation_windows"])[name]
                    for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY):
                        before = getattr(binding, quantity.value)
                        after = getattr(confirmation, quantity.value)
                        self.assertEqual(len(after.current), detector.p["curve_original_samples"],
                                         msg=diagnostic)
                        self.assertGreater(after.current[0][0], before.newest_at, msg=diagnostic)
                        self.assertLessEqual(after.newest_at, opening.trace_at, msg=diagnostic)
                        for stamp, value in after.previous + after.current:
                            second = (stamp - T0).total_seconds()
                            fall = min(20, max(0, second - 30))
                            rate = .12 if name == "upper" else lower_rate
                            expected = (80 - rate * fall if quantity == Quantity.TEMPERATURE
                                        else 30 - .08 * fall)
                            self.assertAlmostEqual(value, expected, msg=diagnostic)

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
            # This diagnostic robustness case explicitly records full traces.
            await runtime.start_archive(path, "outlier-entry", trace_profile="analysis")
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

    def _thermal_humidity_cadence(self, detector):
        gap = max(detector.p[f"curve_original_gap_humidity_{position.value}_seconds"]
                  for position in detector.positions)
        cadence = (ceil(gap / detector.grid_seconds) + 1) * detector.grid_seconds
        self.assertGreater(cadence, gap)
        self.assertLess(cadence, detector.p["sensor_timeout_seconds"])
        return cadence


    @staticmethod
    def _humidity_for_water(temperature, water):
        return water * WATER_VAPOR_GAS_CONSTANT * (temperature + 273.15) * 100 / (
            saturation_vapor_pressure(temperature) * 1000)

    def _ventilation_curve(self, detector, *, water_tail="plateau", temperature_tail="fall"):
        """Opening, RH flattening, then later water flattening while T falls."""
        count, step = detector.p["curve_original_samples"], detector.grid_seconds
        begin, width = (2 * count + 1) * step, (2 * count + 2) * step
        humidity_plateau, plateau, end = begin + width, begin + 2 * width, begin + 3 * width
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
            if second <= humidity_plateau or water_tail == "fall":
                water = initial_water + (fallen_water - initial_water) * elapsed / width
            elif second <= plateau or water_tail == "rh_plateau":
                return temperature, fallen_h
            else:
                water = absolute_humidity(values(plateau)[0], fallen_h)
            return temperature, self._humidity_for_water(temperature, water)

        return int(begin), int(plateau), int(end), values

    def _feed_ventilation(self, detector, through, values, *, positions=None,
                          missing=(), source=None):
        positions = detector.positions if positions is None else positions
        output = []
        final_index = int(through // detector.grid_seconds)
        for index in range(detector.index + 1, final_index + 1):
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

    def _feed_pending_completion(self, detector, through, values, *, positions=None):
        """Stop at the bound Stage 2 prefix before supplying confirmation."""
        return self._feed_pending_ventilation(detector, through, values, positions=positions,
                                              stage="absolute_humidity")

    def _feed_pending_ventilation(self, detector, through, values, *, positions=None,
                                  stage="relative_humidity"):
        key, confirmed, kind = (
            ("original_ventilation", "original_ventilation_complete", Kind.VENTILATION_COMPLETE)
            if stage == "absolute_humidity" else
            ("original_ventilation_rh", "original_ventilation_rh_confirmed", Kind.VENTILATION)
        )
        output = []
        final_index = int(through // detector.grid_seconds)
        for index in range(detector.index + 1, final_index + 1):
            second = index * detector.grid_seconds
            output += self._feed_ventilation(detector, second, values, positions=positions)
            self.assertNotIn(kind, [item.kind for item in output])
            opening = detector.door_episode
            if opening is not None and key in opening:
                self.assertNotIn(confirmed, opening)
                record = detector.diagnostic["original_ventilation"][stage]
                self.assertTrue(record["initial_form"])
                self.assertFalse(record["confirmable"])
                return second, output
        self.fail(f"The ventilation fixture did not bind an unconfirmed {stage} candidate")

    def _segment(self, detector, duration, before, after=None, *, positions=None,
                 missing=(), heating=False, source=None, stop_when=None, humidity_cadence=None):
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
                    if (quantity == Quantity.HUMIDITY and humidity_cadence is not None
                            and second % humidity_cadence != 0):
                        continue
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
        self.assertEqual(close.channels, ("upper",))


    def test_close_recovers_without_humidity_pairs(self):
        detector = self._entry_detector()
        events = self._opened_entry_episode(detector)
        missing = tuple((position, Quantity.HUMIDITY) for position in detector.positions)
        events += self._segment(detector, 30, (78, 38), (80, 38), missing=missing)
        close = next(item for item in events if item.kind is Kind.DOOR_CLOSE)
        self.assertEqual(close.channels, ("upper",))


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
        moisture_onset = detector.moisture_states["weak"]["onset_at"]
        self.assertIsNotNone(moisture_onset)
        begin, water_plateau, _, values = self._ventilation_curve(detector)
        width = (water_plateau - begin) // 2
        initial_t, initial_h = values(begin)
        fallen_t, fallen_h = values(begin + width)
        temperature_loss, humidity_loss = (initial_t - fallen_t) / 4, (initial_h - fallen_h) / 4
        # A real short RH-led opening retains the already positive long weak
        # window; it must not create a new moisture onset after closure.
        observed = []
        detector.observer = lambda row: observed.append(opening_diagnostic(detector, row))
        cold, dry = 80 - temperature_loss, humidity - humidity_loss
        events += self._segment(detector, width, (80, humidity), (cold, dry), heating=True)
        recovered_humidity = dry + .02 * width
        events += self._segment(detector, width, (cold, dry),
                                (80, recovered_humidity), heating=True)
        events += self._segment(detector, duration, (80, recovered_humidity),
                                (80, recovered_humidity + .02 * duration), heating=True)
        doors = [item for item in events if item.kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE)]
        self.assertEqual([item.kind for item in doors], [Kind.DOOR_OPEN, Kind.DOOR_CLOSE],
                         msg=json.dumps(observed, default=str, sort_keys=True))
        self.assertLess(moisture_onset, doors[0].effective_at)
        self.assertEqual(detector.moisture_states["weak"]["onset_at"], moisture_onset)
        self.assertNotIn(Kind.PERSON_WEAK, [item.kind for item in events])


    def test_source_gap_restarts_strong_hold_after_a_complete_window(self):
        detector = Detector(detection_parameters(), T0, positions=(Position.UPPER,))
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


    def test_lower_loss_keeps_the_upper_person_hold_and_confirmation(self):
        detector = Detector(detection_parameters(), T0)
        reference = Detector(detection_parameters(), T0)
        p = detector.p
        step = p["person_step_seconds"]
        first_full = ceil((p["median_seconds"] - 1 + p["strong_window_seconds"]) / step) * step
        actual, expected = [], []
        for second in range(ceil(first_full) + 1):
            actual += sample(detector, second, 80, 30 + .03 * second)
            expected += sample(reference, second, 80, 30 + .03 * second)
        self.assertGreater(detector.diagnostic["holds"].get("strong", 0), 0)
        self.assertEqual(detector._person_owner["strong"], Position.UPPER)
        self.assertNotIn(Kind.PERSON_STRONG, [item.kind for item in actual])
        gap_at = detector.index + 1
        complete = gap_at + ceil(p["strong_hold_seconds"] + 2 * step)
        for second in range(gap_at, complete + 1):
            expected += sample(reference, second, 80, 30 + .03 * second)
            if second == gap_at:
                for position in detector.positions:
                    for quantity, value in ((Quantity.TEMPERATURE, 80),
                                            (Quantity.HUMIDITY, 30 + .03 * second)):
                        self.assertTrue(detector.accept(measurement(
                            position, quantity, None if position == Position.LOWER else value,
                            second,
                        )))
                actual += detector.advance(T0 + timedelta(seconds=second), enabled=True)
            else:
                actual += sample(detector, second, 80, 30 + .03 * second)
            self.assertEqual(actual, expected)
            self.assertEqual(detector.diagnostic["holds"].get("strong", 0),
                             reference.diagnostic["holds"].get("strong", 0))
        strong = [item for item in actual if item.kind == Kind.PERSON_STRONG]
        self.assertEqual(len(strong), 1)
        self.assertEqual(strong[0].channels, (Position.UPPER.value,))

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


    def test_ventilation_stages_confirm_once_in_order_for_original_positions(self):
        for positions in ((Position.UPPER,), (Position.UPPER, Position.LOWER)):
            with self.subTest(positions=positions):
                detector = self._ventilation_detector(positions=positions)
                _, plateau, end, values = self._ventilation_curve(detector)
                events = self._feed_ventilation(detector, plateau, values)
                self.assertTrue(detector.open)
                ventilation = [item for item in events if item.kind == Kind.VENTILATION]
                self.assertEqual(len(ventilation), 1)
                self.assertIn("original_ventilation_rh_confirmed", detector.door_episode)
                events += self._feed_ventilation(detector, end, values)
                complete = [item for item in events if item.kind == Kind.VENTILATION_COMPLETE]
                self.assertEqual(len(ventilation), 1)
                self.assertEqual(len(complete), 1)
                self.assertEqual(ventilation[0].channels,
                                 (Position.UPPER.value,))
                self.assertEqual(complete[0].channels, ventilation[0].channels)
                self.assertLess(ventilation[0].trace_at, T0 + timedelta(seconds=plateau))
                self.assertGreater(complete[0].trace_at, ventilation[0].trace_at)
                self.assertEqual(ventilation[0].effective_at, ventilation[0].trace_at)
                self.assertEqual(complete[0].effective_at, complete[0].trace_at)
                self.assertTrue(detector.ventilated)
                self.assertNotIn(Kind.DOOR_CLOSE, [item.kind for item in events])


    def test_rh_stage_needs_fresh_t_and_rh_but_no_right_hand_water_partner(self):
        detector = self._ventilation_detector()
        _, _, end, values = self._ventilation_curve(detector)
        step = detector.grid_seconds
        pending_at, found = self._feed_pending_ventilation(detector, end, values)
        candidate = detector.door_episode["original_ventilation_rh"]
        self.assertNotIn(Kind.VENTILATION, [item.kind for item in found])

        for received in (pending_at + step, pending_at + 2 * step):
            if received == pending_at + 2 * step:
                for position in detector.positions:
                    for quantity, value in zip(
                        (Quantity.TEMPERATURE, Quantity.HUMIDITY), values(pending_at), strict=True,
                    ):
                        detector.accept(measurement(
                            position, quantity, value, received,
                            measured=T0 + timedelta(seconds=pending_at),
                        ))
            found += detector.advance(T0 + timedelta(seconds=received), enabled=True)
            self.assertIs(detector.door_episode["original_ventilation_rh"], candidate)
            self.assertNotIn(Kind.VENTILATION, [item.kind for item in found])

        temperature_at = pending_at + 3 * step
        humidity_at = pending_at + 4 * step
        for position in detector.positions:
            detector.accept(measurement(
                position, Quantity.TEMPERATURE, values(temperature_at)[0], temperature_at,
                measured=T0 + timedelta(seconds=temperature_at),
            ))
        found += detector.advance(T0 + timedelta(seconds=temperature_at), enabled=True)
        self.assertNotIn(Kind.VENTILATION, [item.kind for item in found])
        for position in detector.positions:
            detector.accept(measurement(
                position, Quantity.HUMIDITY, values(humidity_at)[1], humidity_at,
                measured=T0 + timedelta(seconds=humidity_at),
            ))
        found += detector.advance(T0 + timedelta(seconds=humidity_at), enabled=True)
        ventilation = [item for item in found if item.kind == Kind.VENTILATION]
        self.assertEqual(len(ventilation), 1)
        self.assertEqual(ventilation[0].trace_at, T0 + timedelta(seconds=humidity_at))
        self.assertNotIn(Kind.VENTILATION_COMPLETE, [item.kind for item in found])
        proof = detector.door_episode["original_ventilation_rh_confirmed"]
        for name, temperature, humidity in candidate["newest"]:
            support = dict((item[0], item[1:]) for item in proof["confirmation_originals"])[name]
            self.assertGreater(support[0], temperature)
            self.assertGreater(support[1], humidity)
            self.assertLess(support[0], support[1])

    def test_close_on_same_prefix_prevents_pending_rh_stage_confirmation(self):
        detector = self._ventilation_detector()
        _, braking_at, _, falling = self._ventilation_curve(detector, water_tail="fall")
        count, step = detector.p["curve_original_samples"], detector.grid_seconds
        humidity_plateau = braking_at + 3 * count * step
        pending_at = humidity_plateau + (count - 1) * step
        braking_temperature = falling(braking_at)[0]
        significance = detector.p["curve_original_significance"]

        # Continue the existing fall through a public opening before adding
        # the separate closing brake and the later RH flattening.
        found = self._feed_ventilation(detector, braking_at, falling)
        self.assertTrue(detector.open)
        self.assertEqual([item.kind for item in found].count(Kind.DOOR_OPEN), 1)
        opening = detector.door_episode["original_open"]
        self.assertIn("lowering_witness", opening)
        self.assertLessEqual(opening["confirmation_received_at"],
                             T0 + timedelta(seconds=braking_at))
        self.assertNotIn("original_close", detector.door_episode)

        def values(second):
            temperature, humidity = falling(second)
            if second > braking_at:
                temperature = braking_temperature + (temperature - braking_temperature) / 2
            if second > humidity_plateau:
                humidity = falling(humidity_plateau)[1]
            return temperature, humidity

        # Isolate same-prefix arbitration: declare no discrete pure-braking
        # form, so the real shallower fall cannot close before the target prefix.
        # Sources, owners, windows and freshness remain entirely measurement-driven.
        with patch.object(detector, "_original_temperature_braking_forms", return_value=False):
            found += self._feed_ventilation(detector, braking_at + count * step, values)
            self.assertIn("original_close", detector.door_episode, detector.diagnostic)
            closing = detector.door_episode["original_close"]
            self.assertLessEqual(
                max(stamp for _, stamp in closing["newest"]) + timedelta(seconds=2 * count * step),
                T0 + timedelta(seconds=humidity_plateau),
            )
            # Two complete T blocks must follow the actual close binding before
            # RH even starts flattening; waiting after RH binding could confirm it.
            pending_at, pending_events = self._feed_pending_ventilation(detector, pending_at, values)
        found += pending_events
        self.assertIn("original_ventilation_rh", detector.door_episode)
        self.assertIs(detector.door_episode["original_close"], closing)
        self.assertGreater(closing["received_at"], opening["confirmation_received_at"])
        forms = detector.diagnostic["original_forms"]
        self.assertTrue(forms["complete_fresh_original_block"]["result"], forms)
        self.assertEqual(set(forms["followup"]), {Position.UPPER.value})
        for position in (Position.UPPER,):
            followup = forms["followup"][position.value][Quantity.TEMPERATURE.value]
            self.assertEqual((followup["fresh_previous"], followup["fresh_current"]),
                             (count, count), followup)
        self.assertGreater(forms["bound_temperature_braking"]["value"], significance)
        relative = detector.diagnostic["original_ventilation"]["relative_humidity"]
        self.assertTrue(relative["initial_form"])
        self.assertFalse(relative["confirmable"])
        self.assertNotIn(Kind.DOOR_CLOSE, [item.kind for item in found])
        self.assertNotIn(Kind.VENTILATION, [item.kind for item in found])

        second = pending_at + step
        confirmation_at = T0 + timedelta(seconds=second)
        before_context = detector._original_context()
        target_values = dict(zip(
            (Quantity.TEMPERATURE, Quantity.HUMIDITY), values(second), strict=True,
        ))
        # One measured precondition serves four publication contracts. The
        # thermal comparison is declared only to isolate arbitration; the
        # source history, windows, owners and public callbacks remain real.
        variants = (
            ("rh_then_t", (Quantity.HUMIDITY, Quantity.TEMPERATURE), False, True,
             [Kind.DOOR_CLOSE]),
            ("t_then_rh", (Quantity.TEMPERATURE, Quantity.HUMIDITY), False, True,
             [Kind.DOOR_CLOSE]),
            ("different_original_times", (Quantity.HUMIDITY, Quantity.TEMPERATURE), True, True,
             [Kind.VENTILATION, Kind.DOOR_CLOSE]),
            ("ventilation_without_close", (Quantity.HUMIDITY, Quantity.TEMPERATURE), False, False,
             [Kind.VENTILATION]),
        )
        for name, quantities, distinct_originals, force_close, expected in variants:
            with self.subTest(name=name):
                case = deepcopy(detector)
                bound_close = case.door_episode["original_close"]
                callbacks, published = [], []

                def closing_form(temperatures, **kwargs):
                    form = original_thermal_contrasts(temperatures, **kwargs)
                    if not force_close or form is None or any(
                        window.newest_at < confirmation_at for window in temperatures.values()
                    ):
                        return form
                    return replace(
                        form, braking=2 * significance, warming_min=2 * significance,
                        warming_max=2 * significance,
                    )

                def delivered(item):
                    callbacks.append(item)
                    if item.kind == Kind.VENTILATION:
                        completed = case.door_episode["original_ventilation_rh_confirmed"]
                        published.append((completed["published"], completed["confirmation_originals"]))

                with (patch.object(case, "_original_temperature_braking_forms", return_value=False),
                      patch("custom_components.ha_sauna.core.detector.original_thermal_contrasts",
                            side_effect=closing_form)):
                    for quantity in quantities:
                        original_at = confirmation_at
                        if distinct_originals and quantity == Quantity.HUMIDITY:
                            original_at -= timedelta(seconds=step / 2)
                        for position in case.positions:
                            case.accept(measurement(
                                position, quantity, target_values[quantity], second,
                                measured=original_at,
                            ))
                    batch = case.advance(confirmation_at, enabled=True, on_detection=delivered)
                # Signals already delivered to a consumer cannot be repaired by
                # changing the returned list or final diagnostic afterwards.
                self.assertEqual(callbacks, batch)
                self.assertEqual([item.kind for item in batch if item.kind in (
                    Kind.DOOR_CLOSE, Kind.VENTILATION, Kind.VENTILATION_COMPLETE,
                )], expected)
                self.assertEqual(case._original_context(), before_context)
                self.assertEqual(len(published), expected.count(Kind.VENTILATION))
                for was_published, originals in published:
                    self.assertTrue(was_published)
                    for _, temperature_at, humidity_at in originals:
                        self.assertEqual(humidity_at, confirmation_at - timedelta(
                            seconds=step / 2 if distinct_originals else 0,
                        ))
                        if distinct_originals:
                            self.assertLess(temperature_at, confirmation_at)
                        else:
                            self.assertEqual(temperature_at, confirmation_at)
                if force_close:
                    self.assertEqual(bound_close["confirmation_received_at"], confirmation_at)
                    ventilation = case.diagnostic["original_ventilation"]
                    self.assertTrue(ventilation["close_same_prefix"])
                    self.assertEqual(ventilation["relative_humidity"]["proven"], distinct_originals)
                    if name == "rh_then_t":
                        self.assertTrue(ventilation["relative_humidity"]["confirmable"])
                    self.assertFalse(case.open)
                    self.assertIsNone(case.door_episode)
                else:
                    self.assertTrue(case.open)
                    completed = case.door_episode["original_ventilation_rh_confirmed"]
                    self.assertTrue(completed["published"])
                    self.assertEqual(completed["confirmation_received_at"], confirmation_at)


    def test_carrier_gap_pauses_completion_and_same_source_can_resume_it(self):
        detector = self._ventilation_detector(positions=(Position.UPPER,))
        _, _, end, values = self._ventilation_curve(detector)
        pending_at, events = self._feed_pending_completion(detector, end, values)
        opening, old_epochs = detector.door_episode, detector._original_context()
        gap = pending_at + detector.grid_seconds
        events += self._feed_ventilation(detector, gap, values, missing=(
            (Position.UPPER, Quantity.TEMPERATURE), (Position.UPPER, Quantity.HUMIDITY),
        ))
        self.assertNotIn("original_ventilation", detector.door_episode)
        self.assertIs(detector.door_episode, opening)
        self.assertNotEqual(detector._original_context(), old_epochs)
        count, step = detector.p["curve_original_samples"], detector.grid_seconds
        anchor = max(end, gap + (2 * count + 1) * step)
        gap_water = absolute_humidity(*values(gap))

        def flat_after_gap(second):
            temperature, _ = values(second)
            return temperature, self._humidity_for_water(temperature, gap_water)

        # No new water fall after the gap: a later real reduction must not
        # accidentally turn this source-reset check into another event case.
        events += self._feed_ventilation(detector, anchor, flat_after_gap)
        self.assertNotIn(Kind.VENTILATION_COMPLETE, [item.kind for item in events])
        record = detector.diagnostic["original_ventilation"]
        self.assertEqual(record["water_status"], "ready")
        self.assertFalse(record["absolute_humidity"]["initial_form"])
        for position in detector.positions:
            support = record["paired_support"][position.value]
            self.assertEqual(support["paired_count"], 2 * count)
            self.assertGreater(support["rh_window_start_at"], T0 + timedelta(seconds=gap))
            water = opening["original_ventilation_water"][position]["features"]
            self.assertEqual(len(water.previous), count)
            self.assertEqual(len(water.current), count)
            self.assertLessEqual(abs(water.b), detector.p["curve_original_significance"] * water.e_minus)
            self.assertFalse(detector.diagnostic["decisions"]["ventilation_complete"]["gates"][
                f"{position.value}:previous_fall"
            ]["result"])
        # A plateau after the gap cannot reuse the old falling window. Replay
        # only the new physical fall and plateau, shifted from the current pair.
        begin, _, new_end, fresh = self._ventilation_curve(detector)
        anchor_t, anchor_h = flat_after_gap(anchor)
        fresh_t, fresh_h = fresh(begin)
        water_scale = absolute_humidity(anchor_t, anchor_h) / absolute_humidity(fresh_t, fresh_h)

        def reacquired(second):
            temperature, humidity = fresh(second - anchor + begin)
            shifted_t = temperature + anchor_t - fresh_t
            water = absolute_humidity(temperature, humidity) * water_scale
            return shifted_t, self._humidity_for_water(shifted_t, water)

        events += self._feed_ventilation(detector, anchor + new_end - begin, reacquired)
        self.assertEqual([item.kind for item in events].count(Kind.VENTILATION), 1)
        self.assertEqual([item.kind for item in events].count(Kind.VENTILATION_COMPLETE), 1)
        self.assertIs(detector.door_episode, opening)
        self.assertEqual(detector.door_episode["positions"], detector.positions)


    def test_delayed_humidity_fall_opens_without_inventing_a_water_plateau(self):
        for delay in ("outside_temperature_turn", "one_original"):
            with self.subTest(humidity_delay=delay):
                traces = []
                detector = Detector(detection_parameters(), T0, observer=traces.append)
                step = detector.grid_seconds
                if delay == "outside_temperature_turn":
                    events = self._segment(detector, self._settling_time(detector), (80, 40))
                    begin = detector.index + 1
                    events += self._segment(detector, 20, (80, 40), (78, 40))
                    events += self._segment(detector, 20, (78, 40), (76, 38))
                    turns = (begin, begin + 20)
                    # At the later RH fall, both T windows have the same
                    # negative slope. This input does not assert an opening.
                else:
                    begin, _, end, values = self._ventilation_curve(detector, water_tail="fall")

                    def delayed_humidity(second):
                        # RH starts one original later while the negative T
                        # turn remains in the original windows.
                        return values(second)[0], values(max(0, second - step))[1]

                    events = self._feed_ventilation(detector, end, delayed_humidity)
                    turns = (begin, begin + step)
                diagnostic = self._door_failure_message(
                    events, traces, input_turns_s=turns,
                    original_forms=[
                        ((row["at"] - T0).total_seconds(), row.get("original_forms"),
                         row["decisions"].get("door_open"))
                        for row in traces
                        if begin <= (row["at"] - T0).total_seconds()
                        <= turns[-1] + 2 * detector.p["curve_original_samples"] * step
                    ],
                )
                self.assertNotIn(Kind.VENTILATION_COMPLETE, [item.kind for item in events], msg=diagnostic)
                if delay == "one_original":
                    self.assertTrue(detector.open, msg=diagnostic)
                    self.assertEqual([item.kind for item in events].count(Kind.DOOR_OPEN), 1,
                                     msg=diagnostic)
                    self.assertEqual(detector.door_episode["positions"], (Position.UPPER,),
                                     msg=diagnostic)
                    self.assertNotIn(Kind.DOOR_CLOSE, [item.kind for item in events], msg=diagnostic)


    def test_temperature_plateau_or_recovery_does_not_confirm_either_ventilation_stage(self):
        prepared = self._ventilation_detector()
        begin, plateau, end, values = self._ventilation_curve(prepared)
        humidity_plateau = begin + (plateau - begin) // 2
        events = self._feed_ventilation(prepared, humidity_plateau, values)
        self.assertNotIn(Kind.VENTILATION, [item.kind for item in events])
        for tail in ("plateau", "recovery"):
            with self.subTest(temperature=tail):
                detector = deepcopy(prepared, {id(prepared.p): prepared.p})
                _, _, _, counterform = self._ventilation_curve(detector, temperature_tail=tail)
                found = self._feed_ventilation(detector, end, counterform)
                self.assertNotIn(Kind.VENTILATION, [item.kind for item in found])
                self.assertNotIn(Kind.VENTILATION_COMPLETE, [item.kind for item in found])
                if tail == "recovery":
                    self.assertIn(Kind.DOOR_CLOSE, [item.kind for item in found])




    def test_runtime_positions_confirm_one_position_available_when_the_door_opens(self):
        """Die Runtime behält beide Rollen, obwohl unten vor der Öffnung fehlt."""
        prepared = Detector(parameters(), T0)
        _, plateau, end, values = self._ventilation_curve(prepared)
        initial = self._feed_ventilation(prepared, plateau, values, positions=(Position.UPPER,))
        self.assertEqual(prepared.positions, (Position.UPPER, Position.LOWER))
        self.assertTrue(prepared.open)
        self.assertEqual(prepared.door_episode["positions"], (Position.UPPER,))
        step = prepared.grid_seconds
        lower_stamp = T0 + timedelta(seconds=plateau + step)
        lower_temperature = values(plateau)[0]
        lower_noise = prepared.p["curve_original_noise_temperature_lower"]

        for interleave in ("none", "unavailable", "same_stamp_correction"):
            with self.subTest(lower=interleave):
                detector = deepcopy(prepared, {id(prepared.p): prepared.p})
                opening = detector.door_episode
                events = list(initial)
                observed_pending = False

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
                    if observed_pending or "original_ventilation" not in opening:
                        continue
                    observed_pending = True
                    self.assertIn("original_ventilation", opening)
                    self.assertNotIn(Kind.VENTILATION_COMPLETE, [event.kind for event in events])
                    if interleave == "none":
                        continue
                    isolated = deepcopy(detector, {id(detector.p): detector.p})
                    candidate = isolated.door_episode["original_ventilation"]
                    received = second + step
                    report_lower(isolated, received)
                    found = isolated.advance(T0 + timedelta(seconds=received), enabled=True)
                    self.assertIs(isolated.door_episode["original_ventilation"], candidate)
                    self.assertNotIn(Kind.VENTILATION_COMPLETE, [event.kind for event in found])
                    # A correction of the bound Upper role still discards it.
                    received += step
                    self.assertTrue(isolated.accept(measurement(
                        Position.UPPER, Quantity.HUMIDITY,
                        values(second)[1] + isolated.p["curve_original_noise_humidity_upper"] / 2,
                        received, measured=now,
                    )))
                    found += isolated.advance(T0 + timedelta(seconds=received), enabled=True)
                    self.assertNotIn("original_ventilation", isolated.door_episode)
                    self.assertNotIn(Kind.VENTILATION_COMPLETE, [event.kind for event in found])

                ventilation = [event for event in events if event.kind is Kind.VENTILATION_COMPLETE]
                self.assertTrue(observed_pending)
                self.assertEqual([event.kind for event in events].count(Kind.DOOR_OPEN), 1)
                self.assertEqual([event.kind for event in events].count(Kind.VENTILATION), 1)
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
        self.assertNotIn(Kind.VENTILATION_COMPLETE, [event.kind for event in events])
        self.assertTrue(detector.ventilation_degraded)

    def test_unused_lower_loss_does_not_block_upper_ventilation_completion(self):
        detector = self._ventilation_detector()
        _, plateau, end, values = self._ventilation_curve(detector)
        events = self._feed_ventilation(detector, plateau, values)
        events += self._feed_ventilation(detector, end + detector.p["sensor_timeout_seconds"],
                                         values, positions=(Position.UPPER,))
        complete = [item for item in events if item.kind == Kind.VENTILATION_COMPLETE]
        self.assertEqual(len(complete), 1)
        self.assertEqual(complete[0].channels, ("upper",))
        self.assertEqual(detector.door_episode["positions"], (Position.UPPER,))


    def test_source_replacement_during_opening_invalidates_its_reference(self):
        detector = self._ventilation_detector()
        _, plateau, end, values = self._ventilation_curve(detector)
        events = self._feed_ventilation(detector, plateau, values)
        events += self._feed_ventilation(detector, end, values, source="sensor.replaced")
        self.assertNotIn(Kind.VENTILATION_COMPLETE, [item.kind for item in events])
        self.assertIn(Position.UPPER, detector.ventilation_invalid)

    def test_completion_needs_new_paired_originals_not_ticks_or_same_stamp_corrections(self):
        detector = self._ventilation_detector()
        _, _, end, values = self._ventilation_curve(detector)
        step = detector.grid_seconds
        pending_at, found = self._feed_pending_completion(detector, end, values)
        self.assertNotIn(Kind.VENTILATION_COMPLETE, [item.kind for item in found])
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
        self.assertNotIn(Kind.VENTILATION_COMPLETE, [item.kind for item in found])
        report(pending_at + 4 * step, pending_at + 4 * step, (Quantity.TEMPERATURE,))
        self.assertNotIn(Kind.VENTILATION_COMPLETE, [item.kind for item in found])
        # New RH has no right-hand T partner yet. It must remain pending rather
        # than supply freshness by skipping to a previously paired RH original.
        report(pending_at + 5 * step, pending_at + 5 * step, (Quantity.HUMIDITY,))
        self.assertNotIn(Kind.VENTILATION_COMPLETE, [item.kind for item in found])
        report(pending_at + 6 * step, pending_at + 6 * step, (Quantity.TEMPERATURE,))
        ventilation = [item for item in found if item.kind == Kind.VENTILATION_COMPLETE]
        self.assertEqual(len(ventilation), 1)
        self.assertEqual(ventilation[0].trace_at, T0 + timedelta(seconds=pending_at + 6 * step))

    def test_complete_paired_completion_windows_bind_and_confirm_with_trailing_pending_rh(self):
        detector = self._ventilation_detector()
        _, plateau, _, values = self._ventilation_curve(detector)
        count, step = detector.p["curve_original_samples"], detector.grid_seconds
        found = self._feed_ventilation(detector, plateau, values)
        self.assertTrue(detector.open)
        self.assertEqual(detector.door_episode["positions"], (Position.UPPER,))
        self.assertNotIn("original_ventilation", detector.door_episode)
        interval = step / 4
        schedule = (
            (Position.UPPER, Quantity.TEMPERATURE, 1),
            (Position.UPPER, Quantity.HUMIDITY, 2),
            (Position.LOWER, Quantity.TEMPERATURE, 3),
            (Position.LOWER, Quantity.HUMIDITY, 4),
        )
        for position in detector.positions:
            for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY):
                self.assertLessEqual(
                    2 * step,
                    detector.p[f"curve_original_gap_{quantity.value}_{position.value}_seconds"],
                )

        def report(target, second, position, quantity):
            temperature, humidity = values(second)
            self.assertTrue(target.accept(measurement(
                position, quantity, temperature if quantity == Quantity.TEMPERATURE else humidity,
                second, measured=T0 + timedelta(seconds=second),
            )))

        # Alternating U-T/U-RH/L-T/L-RH receipts leave the other position's
        # later RH pending whenever a T completes the preceding RH pair.
        for index in range(1, count + 1):
            second = plateau + index * step
            for position, quantity, offset in schedule:
                report(detector, second - step + offset * interval, position, quantity)
            found += detector.advance(T0 + timedelta(seconds=second), enabled=True)
            if "original_ventilation" in detector.door_episode:
                break
        self.assertIn("original_ventilation", detector.door_episode)
        self.assertNotIn(Kind.VENTILATION_COMPLETE, [item.kind for item in found])
        candidate = detector.door_episode["original_ventilation"]
        self.assertGreater(candidate["received_at"], T0 + timedelta(seconds=plateau + interval))
        bound = detector.diagnostic["original_ventilation"]
        self.assertEqual(bound["water_status"], "pending")
        self.assertTrue(bound["absolute_humidity"]["initial_form"])
        self.assertEqual(tuple(name for name, _ in candidate["water_windows"]), ("upper",))
        for _, water in candidate["water_windows"]:
            self.assertEqual(len(water.previous), count)
            self.assertEqual(len(water.current), count)
        for support in bound["paired_support"].values():
            self.assertEqual(support["paired_count"], 2 * count)
            self.assertGreater(support["pending_count"], 0)
            self.assertGreater(support["pending_rh_at"], support["rh_newest_at"])

        next_second = second + step
        humidity_only = deepcopy(detector, {id(detector.p): detector.p})
        unchanged = humidity_only.door_episode["original_ventilation"]
        for position, quantity, offset in schedule:
            if quantity == Quantity.HUMIDITY:
                report(humidity_only, second + offset * interval, position, quantity)
        output = humidity_only.advance(T0 + timedelta(seconds=next_second), enabled=True)
        self.assertNotIn(Kind.VENTILATION_COMPLETE, [item.kind for item in output])
        self.assertIs(humidity_only.door_episode["original_ventilation"], unchanged)
        unpaired = humidity_only.diagnostic["original_ventilation"]
        self.assertFalse(unpaired["absolute_humidity"]["confirmable"])
        for name, support in unpaired["paired_support"].items():
            self.assertEqual(support["rh_newest_at"], bound["paired_support"][name]["rh_newest_at"])
            self.assertEqual(
                unpaired["absolute_humidity"]["support"][name]["temperature_newest_at"],
                bound["absolute_humidity"]["support"][name]["temperature_newest_at"],
            )

        for positions in ((Position.UPPER,), detector.positions):
            with self.subTest(new_pairs=positions):
                continued = deepcopy(detector, {id(detector.p): detector.p})
                for position, quantity, offset in schedule:
                    if position in positions:
                        report(continued, second + offset * interval, position, quantity)
                output = continued.advance(T0 + timedelta(seconds=next_second), enabled=True)
                record = continued.diagnostic["original_ventilation"]
                ventilation = [item for item in output if item.kind == Kind.VENTILATION_COMPLETE]
                self.assertEqual(len(ventilation), 1)
                self.assertEqual(ventilation[0].channels,
                                 (Position.UPPER.value,))
                self.assertEqual(ventilation[0].trace_at, T0 + timedelta(seconds=next_second))
                self.assertTrue(record["absolute_humidity"]["confirmable"])
                paired_support = record["absolute_humidity"]["paired_support"]
                for name, temperature, humidity in candidate["newest"]:
                    support = paired_support[name]
                    self.assertEqual(support["paired_count"], 2 * count)
                    self.assertGreater(
                        record["absolute_humidity"]["support"][name]["temperature_newest_at"],
                        temperature,
                    )
                    self.assertGreater(support["rh_newest_at"], humidity)
                upper = paired_support["upper"]
                self.assertGreater(upper["pending_count"], 0)
                self.assertGreater(upper["pending_rh_at"], upper["rh_newest_at"])

    def test_original_gap_keeps_pending_water_evidence_without_bridging(self):
        detector = self._ventilation_detector()
        _, _, end, values = self._ventilation_curve(detector)
        step = detector.grid_seconds
        pending_at, found = self._feed_pending_completion(detector, end, values)
        opening = detector.door_episode
        candidate = opening["original_ventilation"]
        bound_at = candidate["received_at"]
        bound_windows = candidate["humidity_windows"], candidate["temperature_windows"]
        owner = opening["positions"][0]
        humidity_support = detector._original_time_support_cache[owner, Quantity.HUMIDITY][2]
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
            self.assertIs(detector.door_episode, opening)
            self.assertIs(opening.get("original_ventilation"), candidate)
            self.assertEqual(candidate["received_at"], bound_at)
            self.assertEqual(
                (candidate["humidity_windows"], candidate["temperature_windows"]), bound_windows,
            )
            self.assertNotIn(Kind.VENTILATION_COMPLETE, [item.kind for item in found])
        self.assertFalse(humidity_support.fresh(T0 + timedelta(seconds=received - step)),
                         "The existing waiting interval must exceed the carrier's RH cadence")
        # The received RH original exposes an unobserved interval; it is
        # neither a physical counterform nor permission to bridge that gap.
        found += self._feed_ventilation(detector, received, values)
        self.assertIs(detector.door_episode, opening)
        self.assertIs(opening.get("original_ventilation"), candidate)
        self.assertEqual(candidate["received_at"], bound_at)
        self.assertEqual(
            (candidate["humidity_windows"], candidate["temperature_windows"]), bound_windows,
        )
        new_support = detector._original_time_support_cache[owner, Quantity.HUMIDITY][2]
        self.assertFalse(new_support.covers(humidity_support.newest_at, new_support.newest_at))
        self.assertEqual(detector._original_context(), epochs)
        self.assertNotIn(Kind.VENTILATION_COMPLETE, [item.kind for item in found])

    def test_earlier_ventilation_precedes_later_close_within_one_original_batch(self):
        detector = self._ventilation_detector()
        _, plateau, end, values = self._ventilation_curve(detector)
        count, step = detector.p["curve_original_samples"], detector.grid_seconds
        pending_at, found = self._feed_pending_completion(detector, end, values)
        self.assertNotIn(Kind.VENTILATION_COMPLETE, [item.kind for item in found])
        interval = step / (2 * count + 4)
        first_at = pending_at + interval
        first_t, first_h = values(first_at)
        water = absolute_humidity(first_t, first_h)
        opening_temperature_loss = values(0)[0] - values(plateau)[0]
        originals = []
        epochs, rejected = detector._original_context(), detector.rejected
        for index in range(2 * count + 3):
            second = first_at + index * interval
            temperature = first_t + opening_temperature_loss * index / (2 * count + 2)
            humidity = self._humidity_for_water(temperature, water)
            for position in detector.positions:
                for quantity, value in ((Quantity.TEMPERATURE, temperature),
                                        (Quantity.HUMIDITY, humidity)):
                    original = measurement(
                        position, quantity, value, second,
                        measured=T0 + timedelta(seconds=second),
                    )
                    originals.append(original)
                    self.assertTrue(detector.accept(original))
        self.assertEqual(tuple(detector.pending), tuple(originals))
        completion_originals = []

        def received(detection):
            if detection.kind is Kind.VENTILATION_COMPLETE:
                completion_originals.append(
                    detector.door_episode["original_ventilation_complete"]["confirmation_received_at"],
                )

        raster_at = T0 + timedelta(seconds=pending_at + step)
        batch = detector.advance(raster_at, enabled=True, on_detection=received)
        doors = [item for item in batch if item.kind in (Kind.VENTILATION_COMPLETE, Kind.DOOR_CLOSE)]
        self.assertEqual([item.kind for item in doors], [Kind.VENTILATION_COMPLETE, Kind.DOOR_CLOSE])
        self.assertEqual(len(completion_originals), 1)
        close_original = detector.diagnostic["original_forms"]["confirmation_received_at"]
        original_times = {item.received_at for item in originals}
        self.assertIn(completion_originals[0], original_times)
        self.assertIn(close_original, original_times)
        self.assertLess(completion_originals[0], close_original)
        self.assertLess(close_original, raster_at)
        # A later original confirms the close after COMPLETE, while its
        # retrospectively attributed onset remains at 52 s, with COMPLETE at 53 s.
        self.assertEqual(doors[0].effective_at, T0 + timedelta(seconds=53))
        self.assertEqual(doors[1].effective_at, T0 + timedelta(seconds=52))
        self.assertGreater(doors[0].effective_at, doors[1].effective_at)
        self.assertEqual(tuple(item.trace_at for item in doors), (raster_at, raster_at))
        self.assertEqual(tuple(item.detected_at for item in doors), (raster_at, raster_at))
        self.assertFalse(detector.pending)
        self.assertFalse(detector._original_prefixes)
        self.assertEqual(detector._original_context(), epochs)
        self.assertEqual(detector.rejected, rejected)
        for position in detector.positions:
            for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY):
                accepted = tuple((item.measured_at, item.value) for item in originals
                                 if item.position is position and item.quantity is quantity)
                points = tuple(detector._curve_series[position, quantity]["points"])
                self.assertEqual(points[-len(accepted):], accepted)
        self.assertEqual([item.kind for item in found + batch].count(Kind.VENTILATION_COMPLETE), 1)
        self.assertEqual([item.kind for item in found + batch].count(Kind.DOOR_CLOSE), 1)
        self.assertFalse(detector.open)
        self.assertIsNone(detector.door_episode)

    def test_fresh_rh_fall_binds_with_constant_t_and_waits_for_later_cooling(self):
        detector = self._ventilation_detector()
        begin, plateau, _, values = self._ventilation_curve(detector, water_tail="fall")
        width = (plateau - begin) // 2

        def delayed_cooling(second):
            return values(max(begin, second - width))[0], values(second)[1]

        events = self._feed_ventilation(detector, begin + width, delayed_cooling)
        self.assertNotIn(Kind.DOOR_OPEN, [item.kind for item in events])
        self.assertFalse(detector.open)
        opening = detector.door_episode
        candidate = opening["original_open"]
        self.assertTrue(all(pair.temperature.v == 0 for _, pair in candidate["windows"]))
        self.assertGreater(candidate["humidity_fall_onset"],
                           detector.p["curve_original_significance"])
        events += self._feed_ventilation(detector, plateau, delayed_cooling)
        self.assertEqual([item.kind for item in events].count(Kind.DOOR_OPEN), 1)
        self.assertIs(detector.door_episode, opening)
        self.assertIs(opening["original_open"], candidate)
        self.assertGreater(candidate["confirmation_received_at"], candidate["received_at"])

    def test_temperature_hinge_with_valid_rh_does_not_start_an_opening_even_while_heating(self):
        for heating in (False, True):
            with self.subTest(heating=heating):
                detector = self._ventilation_detector()
                _, _, end, values = self._ventilation_curve(detector, water_tail="fall")
                detector.report_heating(heating, T0)
                events = self._feed_ventilation(
                    detector, end, lambda second: (values(second)[0], values(0)[1]),
                )
                self.assertNotIn(Kind.DOOR_OPEN, [item.kind for item in events])
                self.assertFalse(detector.open)
                self.assertIsNone(detector.door_episode)

    def test_rh_flat_tail_releases_pending_and_later_fall_binds_a_new_opening(self):
        traces = []
        detector = Detector(detection_parameters(), T0, observer=traces.append)
        begin, plateau, end, values = self._ventilation_curve(detector)
        width = (plateau - begin) // 2
        humidity_plateau = begin + width

        def first_fall(second):
            return values(0)[0], values(min(second, humidity_plateau))[1]

        events = self._feed_ventilation(detector, humidity_plateau, first_fall)
        previous = detector.door_episode["original_open"]
        self.assertFalse(detector.open)
        events += self._feed_ventilation(detector, plateau, first_fall)
        self.assertIsNone(detector.door_episode)
        self.assertTrue(any(
            row.get("original_forms", {}).get("opening_candidate_reset_reason") == "humidity_fall_ended"
            for row in traces
        ))
        initial_h, fallen_h = values(0)[1], values(humidity_plateau)[1]

        def next_fall(second):
            return values(0)[0], fallen_h + (fallen_h - initial_h) * (second - plateau) / width

        events += self._feed_ventilation(detector, end, next_fall)
        current = detector.door_episode["original_open"]
        self.assertGreater(current["received_at"], previous["received_at"])
        self.assertGreater(current["received_at"], T0 + timedelta(seconds=plateau))
        self.assertNotIn(Kind.DOOR_OPEN, [item.kind for item in events])


    def test_rising_humidity_blocks_temperature_only_opening_during_continuous_heating(self):
        detector = Detector(detection_parameters(), T0)
        events = []
        for i in range(80):
            detector.report_heating(True, T0 + timedelta(seconds=i))
            # A valid rising RH window prevents the temperature-only heating route.
            temperature = 50 if i < 20 else 50-.08*min(20,i-20)+max(0,i-40)*.03
            events += sample(detector, i, temperature, 30+i*.002)
        self.assertNotIn(Kind.DOOR_OPEN, [item.kind for item in events])
        self.assertIsNone(detector.door_episode)

    def test_native_and_thermal_openings_need_a_later_original_after_first_binding(self):
        for route in ("mixed", "thermal"):
            with self.subTest(route=route):
                detector = Detector(detection_parameters(), T0, (Position.UPPER,))
                step = detector.grid_seconds
                thermal = route == "thermal"
                cadence = self._thermal_humidity_cadence(detector) if thermal else None
                initial_h = 30 if thermal else 80
                events = self._segment(detector, self._settling_time(detector), (90, initial_h),
                                       heating=thermal, humidity_cadence=cadence)
                key = ("door_open_heating", Position.UPPER, Quantity.TEMPERATURE)
                proposal_key = ("door_open_heating", detector.positions, (Quantity.TEMPERATURE,))

                def pending():
                    return not detector.open and (
                        proposal_key in detector._curve_proposals if thermal else
                        bool(detector.door_episode and detector.door_episode.get("original_open"))
                    )

                duration = detector.p["curve_window_seconds"]
                events += self._segment(
                    detector, duration, (90, initial_h),
                    (90 - .04 * duration, initial_h if thermal else initial_h - .06 * duration),
                    heating=thermal, humidity_cadence=cadence, stop_when=pending,
                )
                self.assertTrue(pending())
                self.assertNotIn(Kind.DOOR_OPEN, [item.kind for item in events])
                bound_at = detector.index * step
                temperature = detector.latest[Position.UPPER, Quantity.TEMPERATURE].value
                humidity = detector.latest[Position.UPPER, Quantity.HUMIDITY].value
                if thermal:
                    witness = detector._curve_candidates[key]
                    self.assertIsNone(witness.fresh_newest_at)
                    self.assertIsNone(detector._curve_proposals[proposal_key].evidence_bound_at)
                else:
                    witness = detector.door_episode["original_open"]
                    self.assertNotIn("confirmation_received_at", witness)
                tick = detector.advance(T0 + timedelta(seconds=bound_at + step), enabled=True)
                self.assertNotIn(Kind.DOOR_OPEN, [item.kind for item in tick])
                received = bound_at + 2 * step
                events += sample(
                    detector, received, temperature - .04 * 2 * step,
                    humidity if thermal else humidity - .06 * 2 * step,
                    detector.positions, humidity_cadence=cadence,
                )
                openings = [item for item in events if item.kind == Kind.DOOR_OPEN]
                self.assertEqual(len(openings), 1)
                self.assertEqual(openings[0].trace_at, T0 + timedelta(seconds=received))
                if thermal:
                    consumed = detector._curve_proposal_status[proposal_key]
                    self.assertEqual(consumed["status"], "consumed")
                    self.assertIn(witness.candidate_id, consumed["candidate_ids"])
                    confirmation = next(item for item in consumed["confirmation"]
                                        if item["candidate_id"] == witness.candidate_id)
                    self.assertGreater(confirmation["fresh_newest_at"], witness.first_newest_at)
                else:
                    self.assertIs(detector.door_episode["original_open"], witness)
                    self.assertGreater(witness["confirmation_received_at"], witness["received_at"])
                    original = {name: (temperature, humidity)
                                for name, temperature, humidity in witness["newest"]}
                    for name, pair in witness["confirmation_windows"]:
                        self.assertGreater(pair.temperature.newest_at, original[name][0])
                        self.assertGreater(pair.humidity.newest_at, original[name][1])
                tail = self._segment(
                    detector, 2 * detector.p["curve_original_samples"] * step,
                    (temperature - .04 * 2 * step, humidity if thermal else humidity - .06 * 2 * step),
                    (temperature - .04 * (2 + 2 * detector.p["curve_original_samples"]) * step,
                     humidity if thermal else humidity - .06 * (2 + 2 * detector.p["curve_original_samples"]) * step),
                    heating=thermal, humidity_cadence=cadence,
                )
                self.assertNotIn(Kind.DOOR_OPEN, [item.kind for item in tail])


    def test_ticks_and_repeated_originals_cannot_confirm_a_stale_opening_candidate(self):
        for route in ("mixed", "thermal"):
            with self.subTest(route=route):
                detector = Detector(detection_parameters(), T0, (Position.UPPER,))
                step = detector.grid_seconds
                thermal = route == "thermal"
                cadence = self._thermal_humidity_cadence(detector) if thermal else None
                initial_h = 30 if thermal else 80
                events = self._segment(detector, self._settling_time(detector), (90, initial_h),
                                       heating=thermal, humidity_cadence=cadence)
                proposal_key = ("door_open_heating", detector.positions, (Quantity.TEMPERATURE,))

                def pending():
                    return not detector.open and (
                        proposal_key in detector._curve_proposals if thermal else
                        bool(detector.door_episode and detector.door_episode.get("original_open"))
                    )

                duration = detector.p["curve_window_seconds"]
                events += self._segment(
                    detector, duration, (90, initial_h),
                    (90 - .04 * duration, initial_h if thermal else initial_h - .06 * duration),
                    heating=thermal, humidity_cadence=cadence, stop_when=pending,
                )
                self.assertTrue(pending())
                self.assertNotIn(Kind.DOOR_OPEN, [item.kind for item in events])
                bound_at = detector.index * step
                originals = {quantity: tuple(detector._curve_series[Position.UPPER, quantity]["points"])
                             for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY)}
                # Equal receipt timestamps are accepted by the existing boundary
                # contract; consuming them on the next tick adds no original.
                for quantity in ((Quantity.TEMPERATURE,) if thermal else
                                 (Quantity.TEMPERATURE, Quantity.HUMIDITY)):
                    self.assertTrue(detector.accept(detector.latest[Position.UPPER, quantity]))
                events += detector.advance(T0 + timedelta(seconds=bound_at + step), enabled=True)
                gap = max(detector.p[f"curve_original_gap_{quantity.value}_upper_seconds"]
                          for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY))
                stale_at = bound_at + (ceil(gap / step) + 1) * step
                self.assertLess(stale_at - bound_at, detector.p["sensor_timeout_seconds"])
                events += detector.advance(T0 + timedelta(seconds=stale_at), enabled=True)
                self.assertFalse(detector.open)
                self.assertNotIn(Kind.DOOR_OPEN, [item.kind for item in events])
                for quantity, points in originals.items():
                    self.assertEqual(tuple(detector._curve_series[Position.UPPER, quantity]["points"]),
                                     points)
                if thermal:
                    witness = detector._curve_candidates["door_open_heating", Position.UPPER,
                                                         Quantity.TEMPERATURE]
                    self.assertIsNone(witness.fresh_newest_at)
                    self.assertNotEqual(detector._curve_proposal_status[proposal_key]["status"],
                                        "consumed")
                else:
                    # A valid current sensor remains available even when its
                    # original window is too old to supply a fresh confirmation.
                    at = T0 + timedelta(seconds=stale_at)
                    self.assertTrue(detector._position_available(
                        Position.UPPER, (Quantity.TEMPERATURE, Quantity.HUMIDITY), at,
                    ))
                    # The independent thermal fallback asks for usable RH
                    # original forms, not for permission to replace this sensor.
                    self.assertFalse(detector._original_humidity_available(detector.positions, at))
                    support = detector._original_time_support_cache[
                        Position.UPPER, Quantity.HUMIDITY
                    ][2]
                    self.assertIsNotNone(support)
                    self.assertFalse(support.fresh(at))
                    self.assertNotIn("confirmation_received_at", detector.door_episode["original_open"])


    def test_temperature_rule_does_not_treat_heater_off_as_door_opening(self):
        for heating, lower_falls, duration in ((False, True, 20), (None, True, 20), (True, False, 20), (True, True, 2)):
            with self.subTest(heating=heating, lower_falls=lower_falls, duration=duration):
                d = Detector(detection_parameters(), T0)
                cadence = self._thermal_humidity_cadence(d)
                events = []
                for i in range(70):
                    d.report_heating(heating if i >= 20 else True, T0+timedelta(seconds=i))
                    delta = -.03*(i-20) if 20 <= i < 20+duration else 0
                    for position in (Position.UPPER, Position.LOWER):
                        temp = 50+delta if position == Position.UPPER or lower_falls else 50
                        d.accept(measurement(position,Quantity.TEMPERATURE,temp,i))
                        if i % cadence == 0:
                            d.accept(measurement(position,Quantity.HUMIDITY,30,i))
                    events += d.advance(T0+timedelta(seconds=i),enabled=True)
                openings = [item for item in events if item.kind == Kind.DOOR_OPEN]
                if heating is True and duration == 20:
                    # A steady lower sensor cannot veto the independently
                    # supported upper fall during continuous heating.
                    self.assertEqual(len(openings), 1)
                    self.assertEqual(openings[0].channels, (Position.UPPER.value,))
                else:
                    self.assertEqual(openings, [])

    def test_heater_change_restarts_additional_door_proof(self):
        for positions in ((Position.UPPER,), (Position.UPPER, Position.LOWER)):
            d=Detector(detection_parameters(),T0,positions)
            cadence = self._thermal_humidity_cadence(d)
            events=[]
            for i in range(80):
                # Wiederholtes Aus/Ein verhindert den durchgehenden Nachweis.
                d.report_heating(False if i%8==0 else True,T0+timedelta(seconds=i))
                events+=sample(d,i,50-.03*i,30,positions, humidity_cadence=cadence)
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
        detector = Detector(detection_parameters(), T0, (Position.UPPER,))
        cadence = self._thermal_humidity_cadence(detector)
        baseline = self._settling_time(detector)
        off_second = baseline + 80
        off_at = T0 + timedelta(seconds=off_second)
        controller = Controller(detection_parameters())
        controller.begin_session("heating", T0)
        controller.report_heating(True, T0)
        controller.report_heating(False, off_at)
        # Queue the same warm baseline and falling hinge as the continuous
        # heating fixture, then catch up while the latest feedback is OFF.
        for second in range(off_second + 21):
            fall = max(0, second - baseline + 1)
            detector.accept(measurement(
                Position.UPPER, Quantity.TEMPERATURE, 50 - .03 * fall, second
            ))
            if second % cadence == 0:
                detector.accept(measurement(Position.UPPER, Quantity.HUMIDITY, 30, second))
        events = detector.advance(
            T0 + timedelta(seconds=off_second + 20), enabled=True,
            heating_intervals=controller.session.heating.intervals,
            heating_gates=((T0, True), (off_at, False)),
        )
        openings = [event for event in events if event.kind == Kind.DOOR_OPEN]
        self.assertEqual(len(openings), 1)
        self.assertLess(openings[0].trace_at, off_at)
        self.assertIs(controller.feedback, False)
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

    def test_temperature_loss_invalidates_an_unconfirmed_thermal_identity_before_recovery(self):
        detector = Detector(detection_parameters(), T0, (Position.UPPER,))
        step = detector.grid_seconds
        cadence = self._thermal_humidity_cadence(detector)
        events = self._segment(detector, self._settling_time(detector), (80, 30), heating=True,
                               humidity_cadence=cadence)
        key = ("door_open_heating", Position.UPPER, Quantity.TEMPERATURE)
        proposal_key = ("door_open_heating", detector.positions, (Quantity.TEMPERATURE,))
        duration = detector.p["curve_window_seconds"]

        def pending():
            return not detector.open and proposal_key in detector._curve_proposals

        events += self._segment(detector, duration, (80, 30), (80 - .1 * duration, 30),
                                heating=True, humidity_cadence=cadence, stop_when=pending)
        self.assertTrue(pending())
        original = detector._curve_candidates[key]
        self.assertIsNone(original.fresh_newest_at)
        self.assertIsNone(detector._curve_proposals[proposal_key].evidence_bound_at)
        temperature = detector.latest[Position.UPPER, Quantity.TEMPERATURE].value
        gap_at = (detector.index + 1) * step
        self.assertTrue(detector.accept(measurement(
            Position.UPPER, Quantity.TEMPERATURE, None, gap_at,
        )))
        events += detector.advance(T0 + timedelta(seconds=gap_at), enabled=True)
        self.assertNotIn(key, detector._curve_candidates)
        self.assertNotIn(proposal_key, detector._curve_proposals)
        self.assertFalse(detector.open)
        self.assertNotIn(Kind.DOOR_OPEN, [item.kind for item in events])
        recovery = (2 * max(detector.p["curve_original_samples"],
                            detector.curve_policy.min_side_samples) + 1) * step
        events += self._segment(detector, recovery, (temperature, 30), heating=True,
                                humidity_cadence=cadence)
        self.assertNotIn(key, detector._curve_candidates)
        self.assertNotIn(Kind.DOOR_OPEN, [item.kind for item in events])
        events += self._segment(
            detector, duration, (temperature, 30), (temperature - .1 * duration, 30),
            heating=True, humidity_cadence=cadence,
            stop_when=lambda: key in detector._curve_candidates,
        )
        self.assertIn(key, detector._curve_candidates)
        self.assertFalse(detector.open)
        fresh = detector._curve_candidates[key]
        proposal = detector._curve_proposals[proposal_key]
        self.assertEqual(tuple(candidate.candidate_id for _, candidate in proposal.candidates),
                         (fresh.candidate_id,))
        self.assertIsNone(proposal.evidence_bound_at)
        self.assertNotEqual(fresh.candidate_id, original.candidate_id)
        self.assertNotEqual(fresh.context[1], original.context[1])
        self.assertGreater(fresh.onset_at, T0 + timedelta(seconds=gap_at))
        self.assertTrue(all(stamp > T0 + timedelta(seconds=gap_at)
                            for stamp, _ in fresh.original_points))
        self.assertIsNone(fresh.fresh_newest_at)
        current = detector.latest[Position.UPPER, Quantity.TEMPERATURE].value
        second = (detector.index + 1) * step
        events += sample(detector, second, current - .1 * step, 30, detector.positions,
                         humidity_cadence=cadence)
        # The completed new fall may confirm on its next supporting original.
        # Its own identity and epoch must supply that confirmation, never the
        # pre-loss candidate or the flat recovery interval.
        openings = [item for item in events if item.kind == Kind.DOOR_OPEN]
        self.assertEqual(len(openings), 1)
        self.assertTrue(detector.open)
        opening = openings[0]
        self.assertEqual(opening.channels, (Position.UPPER.value,))
        self.assertEqual(opening.effective_at, fresh.onset_at)
        self.assertEqual(opening.trace_at, T0 + timedelta(seconds=second))
        self.assertEqual(opening.detected_at, opening.trace_at)
        evidence = detector._curve_status[key]
        self.assertEqual(evidence["candidate_id"], fresh.candidate_id)
        self.assertGreater(evidence["fresh_newest_at"], fresh.first_newest_at)
        self.assertEqual(evidence["fresh_observed_at"], opening.trace_at)
        consumed = detector._curve_proposal_status[proposal_key]
        self.assertEqual(consumed["status"], "consumed")
        self.assertEqual(consumed["consumed_candidate_ids"], (fresh.candidate_id,))
        self.assertEqual(consumed["positions"], (Position.UPPER.value,))
        self.assertEqual(tuple(witness["source_epochs"] for witness in consumed["witnesses"]),
                         (fresh.context[1],))
        self.assertTrue(all(witness["supported"] for witness in consumed["confirmation"]))

    def test_additional_door_rule_uses_the_leading_available_sensor(self):
        cases = (
            ("only upper configured", (Position.UPPER,), (Position.UPPER,), (Position.UPPER,), True),
            ("only lower configured", (Position.LOWER,), (Position.LOWER,), (Position.LOWER,), True),
            ("upper survives", (Position.UPPER, Position.LOWER), (Position.UPPER,), (Position.UPPER,), True),
            ("lower survives", (Position.UPPER, Position.LOWER), (Position.LOWER,), (Position.LOWER,), True),
            ("both fall", (Position.UPPER, Position.LOWER), (Position.UPPER, Position.LOWER), (Position.UPPER, Position.LOWER), True),
            ("upper falls", (Position.UPPER, Position.LOWER), (Position.UPPER, Position.LOWER), (Position.UPPER,), True),
        )
        for name, configured, reporting, falling, opens in cases:
            with self.subTest(name=name):
                detector = Detector(detection_parameters(), T0, configured)
                cadence = self._thermal_humidity_cadence(detector)
                events = self._segment(detector, self._settling_time(detector),
                                       (50, 30), positions=reporting, heating=True,
                                       humidity_cadence=cadence)
                self.assertNotIn(Kind.DOOR_OPEN, [event.kind for event in events])
                for index in range(80):
                    second = detector.index + 1
                    detector.report_heating(True, T0 + timedelta(seconds=second))
                    for position in reporting:
                        temperature = 50 - .03 * (index + 1) if position in falling else 50
                        detector.accept(measurement(position, Quantity.TEMPERATURE, temperature, second))
                        if second % cadence == 0:
                            detector.accept(measurement(position, Quantity.HUMIDITY, 30, second))
                    events += detector.advance(T0 + timedelta(seconds=second), enabled=True)
                self.assertEqual(detector.active_positions, reporting)
                self.assertEqual(Kind.DOOR_OPEN in [event.kind for event in events], opens)

    def test_hot_operation_uses_the_continuous_heating_route(self):
        minimum = parameters().values["sauna_min_temperature_c"]
        for base in (parameters().values["standard_temperature_c"], minimum + 5, minimum - 1):
            with self.subTest(base=base):
                detector = Detector(detection_parameters(), T0)
                cadence = self._thermal_humidity_cadence(detector)
                events = self._segment(detector, self._settling_time(detector),
                                       (base, 30), heating=True, humidity_cadence=cadence)
                self.assertNotIn(Kind.DOOR_OPEN, [item.kind for item in events])
                # The retained BIC route needs a measured warm baseline before
                # the falling hinge; a line falling since origin has no turn.
                events += self._segment(detector, 80, (base, 30),
                                        (base - .03 * 80, 30), heating=True, humidity_cadence=cadence)
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
                    direction=None, onset_not_before=None, onset_bounds=None,
                    hinge_order=1):
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
                or (onset_bounds is not None and not onset_bounds[0] <= onset <= onset_bounds[1])
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
        A declared RH rise supplies its fresh kink once. Continued equal
        slopes retain R while Q becomes neutral, preserving the fall fallback.
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
                    slope = after * strength * window.e
                    continued_rise = (
                        quantity == Quantity.HUMIDITY and shape["kind"] == "rise"
                        and window.newest_at > self._at(shape["onset"] + self.side * self.step)
                    )
                    previous_slope = slope if continued_rise else before * strength * window.e_minus
                    features.append(replace(
                        window, v=slope, b=previous_slope, r=0.0,
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
            self._declared_flat_end(fall_one) + (self.side + 3) * self.step
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

    def _declared_flat_end(self, fall):
        # The current K-block first becomes constant at fall + K - 1.
        # The preceding block still crosses the fall/plateau knee; its real
        # residual is retained by _original_forms and rejects that first bind.
        # Both blocks are flat at fall + 2K - 1, then K new originals follow.
        return fall + (3 * self.side - 1) * self.step

    def _kink_originals(self, *, rising, large, extra=0):
        """A curved background, disjoint flat support and a declared linear turn."""
        centered = tuple((index - (self.side - 1) / 2) * self.step
                         for index in range(self.side))
        slope_weight = sum(abs(value) for value in centered) / sum(
            value * value for value in centered
        )
        catalog = 2 * max(self.p[f"curve_original_noise_humidity_{position.value}"]
                          for position in Position) * slope_weight
        history_change = 4 * catalog
        quadratic = history_change / (2 * self.side * self.step)
        history_count = 4 * self.side + 2 * self.p["curve_original_kink_history_windows"]
        onset_index = history_count + 2 * self.side - 1
        onset = onset_index * self.step
        end = onset + (self.side + 2 + extra) * self.step
        slope = (4 * self.p["curve_original_kink_factor"] * history_change if large
                 else 2 * self.p["curve_original_significance"] * catalog)
        cooling = (trace(70)[0] - trace(85)[0]) / self.width
        rows = []
        for second in range(0, end + self.step, self.step):
            if second < history_count * self.step:
                # The historical neighboring K-window slopes differ by the
                # analytic constant 2*q*K*step. T stays flat, so this falling
                # prehistory cannot confirm a door or bind a rising infusion.
                humidity = self.baseline + quadratic * (
                    second - (history_count - 1) * self.step
                ) ** 2
            else:
                humidity = self.baseline + (1 if rising else -1) * slope * max(
                    0, second - onset,
                )
            temperature = self.temperature - (0 if rising else cooling) * max(
                0, second - onset,
            )
            rows.append((second, temperature, humidity))
        return onset, end, rows

    def _feed_kink_originals(self, detector, rows, events, *, batch=False, omit=(),
                             humidity_sources=None, detected_at=None):
        for second, temperature, humidity in rows:
            for position in detector.positions:
                position_humidity = humidity[position] if isinstance(humidity, dict) else humidity
                for quantity, value in ((Quantity.TEMPERATURE, temperature),
                                        (Quantity.HUMIDITY, position_humidity)):
                    if (position, quantity) in omit:
                        continue
                    self.assertTrue(detector.accept(measurement(
                        position, quantity, value, second, measured=self._at(second),
                        source=(humidity_sources or {}).get(position)
                        if quantity == Quantity.HUMIDITY else None,
                    )))
            if not batch:
                events.extend(detector.advance(
                    self._at(second), enabled=True, detected_at=detected_at,
                ))
        finished = ceil(rows[-1][0] / self.step) * self.step
        if batch or rows[-1][0] < finished:
            events.extend(detector.advance(
                self._at(finished), enabled=True, detected_at=detected_at,
            ))

    def test_small_original_kinks_are_rejected_before_door_and_infusion_binding(self):
        for rising in (False, True):
            for control in (False, True):
                with self.subTest(rising=rising, factor_zero=control):
                    onset, end, rows = self._kink_originals(rising=rising, large=False)
                    traces, events = [], []
                    p = detection_parameters(curve_original_kink_factor=0) if control else self.parameters
                    detector = Detector(p, T0, observer=traces.append)
                    # Door confirmation consumes a bound RH onset as well as
                    # the native fall. Declare only that existing fixture knee.
                    shapes = [] if rising else [self._shape("open", onset, end)]
                    with self._patch_math(shapes, forms=original_contrasts):
                        self._feed_kink_originals(detector, rows, events)
                    records = [row["original_infusion" if rising else "original_forms"]
                               for row in traces if row["at"] > self._at(onset)
                               and ("original_infusion" if rising else "original_forms") in row]
                    raw_key = "rise_candidate" if rising else "binding_humidity_fall_onset"
                    kink_key = "binding_humidity_rise_kink" if rising else "binding_humidity_fall_kink"
                    raw_candidates = [record for record in records
                                      if record.get(raw_key, {}).get("result") is True]
                    self.assertTrue(raw_candidates)
                    self.assertEqual(
                        {record[kink_key]["result"] for record in raw_candidates}, {control},
                    )
                    for record in raw_candidates:
                        for gate in record[kink_key]["positions"].values():
                            self.assertEqual(gate["status"], "history")
                            self.assertGreater(gate["history_scale"], gate["catalog_scale"])
                            self.assertLess(gate["history_end"], gate["start_at"])
                            if not control:
                                self.assertLess(abs(gate["delta"]), gate["tolerance"])
                    candidate = (detector._position_state(Position.UPPER)["infusion_candidate"] if rising else
                                 (detector.door_episode or {}).get("original_open"))
                    self.assertEqual(candidate is not None, control)
                    if control:
                        self.assertGreater(candidate["received_at"], self._at(onset))
                        self.assertTrue(all(kink.factor == 0
                                            for _, kink in candidate["humidity_kinks"]))
                    if control and not rising:
                        # Removing the kink tolerance admits a candidate, not
                        # a door event. The small suffix still fails the bound
                        # historical level-loss comparison at both positions.
                        for name, pair in candidate["windows"]:
                            proof = original_decline_evidence(
                                tuple((self._at(second), humidity)
                                      for second, _, humidity in rows),
                                pair.humidity,
                                background=dict(candidate["humidity_kinks"])[name],
                            )
                            self.assertIsNotNone(proof)
                            self.assertLessEqual(proof.level_loss,
                                                 self.p["curve_original_open_contrast"])
                    self.assertEqual([item.kind for item in events
                                      if item.kind in (Kind.DOOR_OPEN, Kind.INFUSION)], [])

        for rising in (False, True):
            for small_position in Position:
                with self.subTest(rising=rising, only_small_role=small_position):
                    onset, _, small = self._kink_originals(rising=rising, large=False)
                    _, _, large = self._kink_originals(rising=rising, large=True)
                    rows = [(second, temperature, {
                        position: small_humidity if position == small_position else large_humidity
                        for position in Position
                    }) for (second, temperature, small_humidity), (_, _, large_humidity)
                            in zip(small, large, strict=True)]
                    traces, events = [], []
                    detector = Detector(self.parameters, T0, observer=traces.append)
                    with self._patch_math([], forms=original_contrasts):
                        self._feed_kink_originals(detector, rows, events)
                    key = "original_infusion" if rising else "original_forms"
                    raw_key = "rise_candidate" if rising else "binding_humidity_fall_onset"
                    kink_key = "binding_humidity_rise_kink" if rising else "binding_humidity_fall_kink"
                    records = [row[key] for row in traces if row["at"] > self._at(onset)
                               and key in row and row[key].get(raw_key, {}).get("result") is True]
                    self.assertTrue(records)
                    # The lower signal remains an actual input, but cannot
                    # veto a clear upper kink or promote an idle upper sensor.
                    reference_traces, reference_events = [], []
                    reference = Detector(self.parameters, T0, (Position.UPPER,),
                                         observer=reference_traces.append)
                    with self._patch_math([], forms=original_contrasts):
                        self._feed_kink_originals(reference, rows, reference_events)
                    reference_records = [row[key] for row in reference_traces
                                         if row["at"] > self._at(onset) and key in row
                                         and row[key].get(raw_key, {}).get("result") is True]
                    self.assertEqual([record[kink_key] for record in records],
                                     [record[kink_key] for record in reference_records])
                    for record in records:
                        self.assertEqual(set(record[kink_key]["positions"]), {"upper"})
                    upper_qualifies = small_position == Position.LOWER
                    self.assertEqual(records[-1][kink_key]["result"], upper_qualifies)
                    candidate = (detector._position_state(Position.UPPER)["infusion_candidate"]
                                 if rising else (detector.door_episode or {}).get("original_open"))
                    self.assertEqual(candidate is not None, upper_qualifies)
                    kinds = (Kind.DOOR_OPEN, Kind.INFUSION)
                    actual = [item for item in events if item.kind in kinds]
                    expected = [item for item in reference_events if item.kind in kinds]
                    self.assertEqual(actual, expected)
                    if not upper_qualifies:
                        self.assertEqual(actual, [])

    def test_synthetic_large_original_kinks_reach_binding_and_confirmation(self):
        # The signal scales with the selected factor. This proves mathematical
        # consumption by the owner; default suitability needs the real replay.
        for rising in (False, True):
            with self.subTest(rising=rising):
                # Retain the same amplitude and background. The falling
                # input supplies one further K-block so its accumulated loss
                # can exceed the actual historical level-change scale.
                onset, end, rows = self._kink_originals(
                    rising=rising, large=True, extra=0 if rising else self.side,
                )
                detector, events = Detector(self.parameters, T0), []
                shapes = [] if rising else [self._shape("open", onset, end)]
                with self._patch_math(shapes, forms=original_contrasts):
                    self._feed_kink_originals(detector, rows, events)
                kind = Kind.INFUSION if rising else Kind.DOOR_OPEN
                detected = [item for item in events if item.kind == kind]
                self.assertEqual(len(detected), 1)
                candidate = (detector._original_infusion_candidate if rising else
                             detector.door_episode["original_open"])
                self.assertIsNotNone(candidate)
                self.assertGreater(candidate["received_at"], self._at(onset))
                for _, kink in candidate["humidity_kinks"]:
                    self.assertEqual(kink.factor, self.p["curve_original_kink_factor"])
                    self.assertGreater(abs(kink.delta), kink.tolerance)
                    self.assertEqual(kink.effective_change > 0, rising)
                confirmation = (candidate["early_confirmation_windows"] if rising else
                                candidate["confirmation_windows"])
                bound = dict(candidate["binding_windows" if rising else "windows"])
                for name, pair in confirmation:
                    self.assertGreater(pair.temperature.newest_at, bound[name].temperature.newest_at)
                    self.assertGreater(pair.humidity.newest_at, bound[name].humidity.newest_at)
                    self.assertLessEqual(pair.humidity.newest_at, detected[0].trace_at)
                if rising:
                    self.assertIs(detector.infusion_episode.confirmed_detection, detected[0])
                    self.assertEqual(detector._infusion_episode["confirmation_basis"], "early_rise")
                else:
                    self.assertTrue(detector.open)
                    confirmed_at = candidate["confirmation_received_at"]
                    self.assertGreater(confirmed_at, candidate["received_at"])
                    for name, pair in candidate["windows"]:
                        proof = original_decline_evidence(
                            tuple((self._at(second), humidity)
                                  for second, _, humidity in rows
                                  if self._at(second) <= confirmed_at),
                            pair.humidity,
                            background=dict(candidate["humidity_kinks"])[name],
                        )
                        self.assertIsNotNone(proof)
                        self.assertGreater(proof.level_loss,
                                           self.p["curve_original_open_contrast"])

    def test_bound_original_kink_background_is_frozen_while_new_originals_arrive(self):
        for rising in (False, True):
            with self.subTest(rising=rising):
                _, end, rows = self._kink_originals(rising=rising, large=True)
                detector, events = Detector(self.parameters, T0), []
                with self._patch_math([], forms=original_contrasts):
                    self._feed_kink_originals(detector, rows, events)
                    candidate = (detector._original_infusion_candidate if rising else
                                 detector.door_episode["original_open"])
                    frozen = candidate["humidity_kinks"]
                    frozen_values = tuple((name, kink.history_start, kink.history_end,
                                           kink.history_scale, kink.used_scale, kink.tolerance)
                                          for name, kink in frozen)
                    kind = Kind.INFUSION if rising else Kind.DOOR_OPEN
                    confirmed = tuple(item for item in events if item.kind == kind)
                    _, _, continued = self._kink_originals(rising=rising, large=True, extra=self.side)
                    self._feed_kink_originals(
                        detector, [row for row in continued if row[0] > end], events,
                    )
                current = (detector._original_infusion_candidate if rising else
                           detector.door_episode["original_open"])
                self.assertIs(current, candidate)
                self.assertIs(current["humidity_kinks"], frozen)
                self.assertEqual(tuple((name, kink.history_start, kink.history_end,
                                        kink.history_scale, kink.used_scale, kink.tolerance)
                                       for name, kink in current["humidity_kinks"]), frozen_values)
                self.assertEqual(tuple(item for item in events if item.kind == kind), confirmed)
                for name, kink in frozen:
                    latest = detector._original_kink_cache[Position(name)][1]
                    self.assertGreater(latest.history_end, kink.history_end)
                    self.assertGreater(latest.window.newest_at, kink.window.newest_at)

    def test_original_kink_binding_and_confirmation_match_individual_and_batch_input(self):
        for rising in (False, True):
            for dense in (False, True):
                with self.subTest(rising=rising, several_originals_per_tick=dense):
                    onset, _, rows = self._kink_originals(
                        rising=rising, large=True, extra=0 if rising else self.side,
                    )
                    if dense:
                        # Several distinct T/RH prefixes share one raster cell;
                        # each must keep the background available on its receipt.
                        onset /= self.side + 2
                        rows = [(second / (self.side + 2), temperature, humidity)
                                for second, temperature, humidity in rows]
                    end = ceil(rows[-1][0] / self.step) * self.step
                    shapes = [] if rising else [self._shape("open", onset, end)]
                    results = []
                    for batch in (False, True):
                        detector, events = Detector(self.parameters, T0), []
                        with self._patch_math(shapes, forms=original_contrasts):
                            self._feed_kink_originals(
                                detector, rows, events, batch=batch, detected_at=self._at(end),
                            )
                        candidate = (detector._original_infusion_candidate if rising else
                                     detector.door_episode["original_open"])
                        binding_key = "binding_windows" if rising else "windows"
                        confirmation_key = ("early_confirmation_windows" if rising else
                                            "confirmation_windows")
                        binding = dict(candidate[binding_key])
                        for name, kink in candidate["humidity_kinks"]:
                            self.assertEqual(kink.window, binding[name].humidity)
                            self.assertLess(kink.history_end, binding[name].humidity.start_at)
                        results.append((events, candidate["received_at"], candidate["newest"],
                                        candidate[binding_key], candidate["humidity_kinks"],
                                        candidate[confirmation_key]))
                    self.assertEqual([item.kind for item in results[0][0]
                                      if item.kind in (Kind.DOOR_OPEN, Kind.INFUSION)],
                                     [Kind.INFUSION if rising else Kind.DOOR_OPEN])
                    self.assertEqual(results[0], results[1])

    def test_original_kink_corrections_invalidate_only_the_dependent_owner(self):
        _, _, rows = self._kink_originals(rising=True, large=True)
        prepared, events = Detector(self.parameters, T0), []
        # The lower RH source exists, but without a lower T it contributes no
        # position to the real upper-only binding or its confirmation.
        omit = ((Position.LOWER, Quantity.TEMPERATURE),)
        with self._patch_math([], forms=original_contrasts):
            self._feed_kink_originals(prepared, rows, events, omit=omit)
        self.assertEqual(len(self._infusions(events)), 1)
        self.assertEqual(prepared._original_infusion_candidate["positions"], (Position.UPPER,))
        for change in ("unrelated_source", "bound_source", "bound_source_before_confirmation",
                       "unrelated_history", "bound_history", "bound_candidate"):
            with self.subTest(change=change):
                correction_events = []
                if change in ("bound_candidate", "bound_source_before_confirmation"):
                    detector = Detector(self.parameters, T0)
                    with self._patch_math([], forms=original_contrasts):
                        for row in rows:
                            self._feed_kink_originals(detector, [row], correction_events, omit=omit)
                            if detector._position_state(Position.UPPER)["infusion_candidate"] is not None:
                                break
                    self.assertIsNone(detector.infusion_episode)
                else:
                    detector = deepcopy(prepared, {id(prepared.p): prepared.p})
                candidate = detector._position_state(Position.UPPER)["infusion_candidate"]
                episode = detector._infusion_episode
                detection = (detector.infusion_episode.confirmed_detection
                             if episode is not None else None)
                kink = dict(candidate["humidity_kinks"])["upper"]
                if change in ("unrelated_source", "bound_source", "bound_source_before_confirmation"):
                    before_confirmation = change == "bound_source_before_confirmation"
                    if before_confirmation:
                        second = (candidate["received_at"] - T0).total_seconds()
                        temperature = detector.latest[Position.UPPER, Quantity.TEMPERATURE].value
                        humidity = detector.latest[Position.UPPER, Quantity.HUMIDITY].value
                    else:
                        second, temperature, humidity = rows[-1]
                    bound_originals = candidate["newest"]
                    confirmation = candidate.get("early_confirmation_windows")
                    # Let a complete new source build its own flat windows.
                    # An old diagnostic object may survive the initial gap,
                    # but cannot bind or confirm using the former source.
                    count = 1 if change == "unrelated_source" else 2 * self.side + 1
                    source_events = []
                    with self._patch_math([], forms=original_contrasts):
                        self._feed_kink_originals(
                            detector, [(second + index * self.step, temperature, humidity)
                                       for index in range(1, count + 1)], source_events, omit=omit,
                            humidity_sources={
                                Position.LOWER if change == "unrelated_source" else Position.UPPER:
                                "sensor.replaced_humidity",
                            },
                        )
                    self.assertEqual(self._infusions(source_events), [])
                    self.assertEqual(candidate["newest"], bound_originals)
                    self.assertEqual(candidate.get("early_confirmation_windows"), confirmation)
                    if change != "unrelated_source":
                        self.assertIsNone(detector._position_state(Position.UPPER)["infusion_candidate"])
                        if episode is not None:
                            self.assertIn(Position.UPPER, episode["interrupted"])
                        else:
                            self.assertIsNone(detector.infusion_episode)
                            self.assertIsNone(confirmation)
                elif change in ("unrelated_history", "bound_history"):
                    # The stored-original correction API is the same explicit
                    # interface special case as the existing Q-correction test;
                    # old measured timestamps cannot enter through accept().
                    history = dict(candidate["humidity_kink_originals"])["upper"]
                    corrected_at, value = next(point for point in history
                                               if point[0] == kink.history_end)
                    detector._correct_original_infusion(
                        Position.LOWER if change == "unrelated_history" else Position.UPPER,
                        Quantity.HUMIDITY, corrected_at,
                        value + self.p["curve_original_noise_humidity_upper"],
                    )
                else:
                    pair = dict(candidate["binding_windows"])["upper"]
                    window = pair.humidity
                    points = window.previous + window.current
                    corrected_at, value = window.current[-1]
                    times = [(stamp - window.current[0][0]).total_seconds()
                             for stamp, _ in window.current]
                    mean = sum(times) / len(times)
                    weight = (times[-1] - mean) / sum((time - mean) ** 2 for time in times)
                    raw_limit = self.p["curve_original_significance"] * (window.e + window.e_minus)
                    target = (raw_limit + kink.tolerance) / 2
                    correction = value - ((window.v - window.b) - target) / weight
                    corrected = original_window(
                        tuple((stamp, correction if stamp == corrected_at else old)
                              for stamp, old in points), count=self.side, scale=window.scale,
                        max_gap_seconds=self.p["curve_original_gap_humidity_upper_seconds"],
                        at=window.newest_at,
                    )
                    proof = original_contrasts({"upper": replace(pair, humidity=corrected)},
                                               expected_positions=("upper",))
                    self.assertGreater(proof.humidity_rise_onset,
                                       self.p["curve_original_significance"])
                    self.assertLess(corrected.v - corrected.b, kink.tolerance)
                    # Correct the actual newest accepted RH original before a
                    # later original can confirm the freshly bound candidate.
                    received = (candidate["received_at"] - T0).total_seconds() + self.step
                    originals = tuple(detector._curve_series[
                        Position.UPPER, Quantity.HUMIDITY
                    ]["points"])
                    self.assertTrue(detector.accept(measurement(
                        Position.UPPER, Quantity.HUMIDITY, correction, received,
                        measured=corrected_at,
                    )))
                    with self._patch_math([], forms=original_contrasts):
                        correction_events.extend(detector.advance(self._at(received), enabled=True))
                    retained = tuple(detector._curve_series[
                        Position.UPPER, Quantity.HUMIDITY
                    ]["points"])
                    self.assertEqual(retained[:-1], originals[:-1])
                    self.assertEqual(retained[-1], (corrected_at, correction))
                    self.assertEqual(self._infusions(correction_events), [])
                dependent = change in ("bound_history", "bound_candidate")
                if dependent:
                    self.assertIsNone(detector._position_state(Position.UPPER)["infusion_candidate"])
                    if episode is not None:
                        self.assertIsNone(episode["original_component"])
                        self.assertIn(Position.UPPER, episode["interrupted"])
                    else:
                        self.assertIsNone(detector.infusion_episode)
                elif change not in ("bound_source", "bound_source_before_confirmation"):
                    self.assertIs(detector._position_state(Position.UPPER)["infusion_candidate"], candidate)
                    self.assertIs(episode["original_component"], candidate)
                if detection is not None:
                    self.assertIs(detector.infusion_episode.confirmed_detection, detection)
                if change not in ("bound_source", "bound_source_before_confirmation", "bound_candidate"):
                    self.assertEqual(detector.infusion_episode.phase, "active")
                self.assertIs(candidate["humidity_kinks"][0][1], kink)

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
        traces = []
        detector = Detector(self.parameters, T0, observer=traces.append)
        events = []
        # This slow person-like ramp uses the real original mathematics and
        # native water pairs; no declared strong R or Q is injected.
        self._feed(detector, end, points, events)
        native = [item["original_infusion"] for item in traces
                  if "original_infusion" in item]
        self.assertTrue(native)
        self.assertTrue(all(
            item["rise"] <= self.p["curve_original_new_infusion_contrast"]
            for item in native
        ))
        self.assertEqual(self._infusions(events), [])
        self.assertIsNone(detector.infusion_episode)
        self.assertIn(Kind.PERSON_STRONG, [item.kind for item in events])

    def test_observed_plateau_ends_pending_rise_before_unrelated_decay(self):
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
            self.assertIsNone(detector._position_state(Position.UPPER)["infusion_candidate"])
            self._feed(detector, unrelated_fall, points, events)
            self.assertIsNone(detector._infusion_episode)
            self.assertEqual(self._infusions(events), [])
            first_rise = next_rise + self.side * self.step
            self._feed(detector, first_rise, points, events)
            rise = detector._position_state(Position.UPPER)["infusion_candidate"]
            self.assertEqual(rise["onsets"][Position.UPPER], self._at(next_rise))
            self.assertFalse(rise["strong"])
            self.assertIsNone(detector._infusion_episode)
            self._feed(detector, first_rise + self.step, points, events)
            self.assertEqual(detector._infusion_episode["phase"], "pending")
            self.assertEqual(detector._infusion_episode["onset_at"], self._at(next_rise))
            self.assertNotEqual(detector.infusion_episode.bound_at, old.bound_at)
            first_decay = next_peak + self.side * self.step
            self._feed(detector, first_decay, points, events)
            self.assertFalse(detector._position_state(Position.UPPER)["infusion_candidate"]["fall_confirmed"])
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
            component = detector._position_state(Position.UPPER)["infusion_candidate"]
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
        self.assertIsNone(detector._position_state(Position.UPPER)["infusion_candidate"])
        self.assertIsNone(detector.infusion_episode)
        self.assertEqual(self._infusions(events), [])

    def test_native_window_smaller_than_curve_flank_keeps_the_matching_early_onset(self):
        minimum = int(BY_KEY["curve_original_samples"].minimum)
        model_flank = int(BY_KEY["curve_min_side_samples"].minimum)
        for count in range(minimum, model_flank):
            with self.subTest(original_samples=count, curve_flank=model_flank):
                self.parameters = detection_parameters(
                    curve_original_samples=count, curve_min_side_samples=model_flank,
                )
                self.p, self.side = self.parameters.values, count
                self.width = (2 * self.side + 2) * self.step
                points, shapes, _, _ = self._scenario(merged=False)
                detector = Detector(self.parameters, T0, (Position.UPPER,))
                events = []
                with self._patch_math(shapes):
                    self._feed(detector, self.begin + self.width, points, events)
                    component = detector._original_infusion_candidate
                    witness = component["onset_witnesses"][Position.UPPER]
                    self.assertEqual(witness.onset_at, self._at(self.begin))
                    self.assertLess(count, detector.curve_policy.min_side_samples)
                    branch = dict(component["rise_windows"])["upper"].humidity.current
                    originals = dict(witness.original_points)
                    self.assertTrue(all(originals.get(stamp) == value for stamp, value in branch))
                    self._feed(detector, self.begin + 2 * self.width, points, events)
                self.assertEqual(len(self._infusions(events)), 1)
                self.assertEqual(self._infusions(events)[0].effective_at, self._at(self.begin))

    def test_unqualified_rh_rise_cannot_gain_water_from_later_warming_on_its_flat_tail(self):
        peak = self.begin + self.width
        warm_start, warm_end = peak + self.width, peak + 2 * self.width
        fall_end = warm_end + self.width
        points = [(0, self.baseline), (self.begin, self.baseline),
                  (peak, self.peak), (warm_end, self.peak), (fall_end, self.level)]
        cold = trace(85)[0]
        temperatures = [(0, self.temperature), (self.begin, self.temperature),
                        (peak, cold), (warm_start, cold), (warm_end, self.temperature)]
        self.assertLess(absolute_humidity(cold, self.peak),
                        absolute_humidity(self.temperature, self.baseline))
        observed = []
        detector = Detector(self.parameters, T0, (Position.UPPER,), observer=observed.append)
        events = []
        self._feed(detector, warm_start, points, events, temperatures=temperatures)
        component = detector._position_state(Position.UPPER)["infusion_candidate"]
        self.assertTrue(component["strong"])
        self.assertTrue(component["rise_ended"])
        self.assertFalse(component["qualified"])
        frontier = component["water_end"][Position.UPPER]
        self.assertLessEqual(frontier, self._at(warm_start))
        self.assertEqual(self._infusions(events), [])
        self._feed(detector, fall_end, points, events, temperatures=temperatures)
        self.assertEqual(component["water_end"][Position.UPPER], frontier)
        self.assertFalse(component["qualified"])
        self.assertLessEqual(component["water"][Position.UPPER]["change"], 0)
        self.assertIsNone(detector.infusion_episode)
        self.assertEqual(self._infusions(events), [])
        self.assertTrue(any(
            row.get("original_infusion", {}).get("water_support_end", {}).get("upper") == frontier
            for row in observed
        ))

    def test_late_t_partner_can_qualify_the_actual_rh_rise_after_its_plateau_begins(self):
        self.side = int(BY_KEY["curve_original_samples"].minimum)
        self.parameters = detection_parameters(curve_original_samples=self.side)
        self.p = self.parameters.values
        self.width = (2 * self.side + 2) * self.step
        peak = self.begin + self.width
        fall_start = peak + self.side * self.step
        fall_end = fall_start + self.width
        points = [(0, self.baseline), (self.begin, self.baseline),
                  (peak, self.peak), (fall_start, self.peak), (fall_end, self.level)]
        shapes = [self._shape("rise", self.begin, peak),
                  self._shape("decay", fall_start, fall_end)]
        detector = Detector(self.parameters, T0, (Position.UPPER,))
        events = []
        with self._patch_math(shapes):
            self._feed(detector, self.begin, points, events)
            detector.accept(measurement(
                Position.UPPER, Quantity.TEMPERATURE, self.temperature,
                self.begin + self.step / 2, measured=self._at(self.begin + self.step / 2),
            ))
            for index in range(1, self.width // self.step + self.side + 1):
                second = self.begin + index * self.step
                if index == self.side + 1:
                    detector.accept(measurement(
                        Position.UPPER, Quantity.TEMPERATURE, self.temperature, second,
                        measured=self._at(self.begin + 3 * self.step / 4),
                    ))
                self._send(detector, second, self._value(points, second), events,
                           omit=((Position.UPPER, Quantity.TEMPERATURE),))
            component = detector._position_state(Position.UPPER)["infusion_candidate"]
            self.assertTrue(component["strong"])
            self.assertTrue(component["rise_ended"])
            self.assertFalse(component["qualified"])
            self.assertTrue(component["water_state"][Position.UPPER]["pending"])
            self.assertEqual(self._infusions(events), [])
            # This new temperature sample brackets the already stored RH rise;
            # no later plateau RH enters the frozen water support.
            received = fall_start + self.step
            partner = peak + self.step
            self.assertLessEqual(
                partner - (self.begin + 3 * self.step / 4),
                self.p["curve_original_gap_temperature_upper_seconds"],
            )
            detector.accept(measurement(
                Position.UPPER, Quantity.TEMPERATURE, self.temperature, received,
                measured=self._at(partner),
            ))
            events.extend(detector.advance(self._at(received), enabled=True))
            self.assertTrue(component["qualified"])
            self.assertGreater(component["water"][Position.UPPER]["change"], 0)
            self._feed(detector, fall_end, points, events)
        self.assertEqual(len(self._infusions(events)), 1)
        self.assertEqual(self._infusions(events)[0].effective_at, self._at(self.begin))

    def test_corrected_rh_end_reopens_water_support_without_confirming_until_new_originals(self):
        self.side = int(BY_KEY["curve_original_samples"].minimum)
        self.parameters = detection_parameters(curve_original_samples=self.side)
        self.p = self.parameters.values
        self.width = (2 * self.side + 2) * self.step
        peak = self.begin + self.width
        end = peak + self.step
        points = [(0, self.baseline), (self.begin, self.baseline),
                  (peak, self.peak), (end, self.peak)]
        shapes = [self._shape("rise", self.begin, peak)]
        detector = Detector(self.parameters, T0, (Position.UPPER,))
        events = []
        with self._patch_math(shapes):
            self._feed(detector, self.begin, points, events)
            detector.accept(measurement(
                Position.UPPER, Quantity.TEMPERATURE, self.temperature,
                self.begin + self.step / 2, measured=self._at(self.begin + self.step / 2),
            ))
            for index in range(1, self.width // self.step + 2):
                second = self.begin + index * self.step
                if index == self.side + 1:
                    detector.accept(measurement(
                        Position.UPPER, Quantity.TEMPERATURE, self.temperature, second,
                        measured=self._at(self.begin + 3 * self.step / 4),
                    ))
                self._send(detector, second, self._value(points, second), events,
                           omit=((Position.UPPER, Quantity.TEMPERATURE),))
            component = detector._position_state(Position.UPPER)["infusion_candidate"]
            self.assertTrue(component["strong"])
            self.assertTrue(component["rise_ended"])
            self.assertFalse(component["qualified"])
            self.assertEqual(component["water_end"][Position.UPPER], self._at(end))
            self.assertIn("upper", dict(component["rise_end_windows"]))
            state = component["water_state"][Position.UPPER]
            pending = dict(state["pending"])
            seen_through = state["seen_through"]
            self.assertTrue(pending)
            self.assertIsNone(state["base"])
            self.assertEqual(self._infusions(events), [])

            continued_peak = end + 2 * self.step
            fall_end = continued_peak + self.width
            increase = (self.peak - self.baseline) * self.step / self.width
            corrected = self.peak + increase
            shapes[0]["until"] = continued_peak
            shapes.append(self._shape("decay", continued_peak, fall_end))
            self._send(detector, end + self.step / 4, corrected, events,
                       measured_second=end, omit=((Position.UPPER, Quantity.TEMPERATURE),))
            # The receipt is between raster steps. Consume its correction
            # before supplying any fresh original or the missing T partner.
            events.extend(detector.advance(self._at(end + self.step), enabled=True))
            self.assertIs(detector._position_state(Position.UPPER)["infusion_candidate"], component)
            self.assertFalse(component["rise_ended"])
            self.assertNotIn(Position.UPPER, component["water_end"])
            self.assertNotIn("upper", dict(component["rise_end_windows"]))
            self.assertIs(component["water_state"][Position.UPPER], state)
            self.assertEqual(state["seen_through"], seen_through)
            self.assertEqual(state["pending"], {**pending, self._at(end): (self._at(end), corrected)})
            self.assertFalse(component["qualified"])
            self.assertIsNone(detector.infusion_episode)
            self.assertEqual(self._infusions(events), [])

            continued = [(end, corrected), (continued_peak, corrected + increase),
                         (fall_end, self.level)]
            self.assertLessEqual(
                continued_peak - (self.begin + 3 * self.step / 4),
                self.p["curve_original_gap_temperature_upper_seconds"],
            )
            self._feed(detector, continued_peak, continued, events)
            self.assertIs(detector._position_state(Position.UPPER)["infusion_candidate"], component)
            self.assertTrue(component["qualified"])
            self.assertGreater(component["water"][Position.UPPER]["change"], 0)
            self.assertGreater(state["seen_through"], self._at(end))
            self.assertGreater(state["maximum"].humidity_sample[0], self._at(end))
            self.assertEqual(state["base"].humidity_sample, component["bases"][Position.UPPER])
            self.assertEqual(state["pending"], {})
            self._feed(detector, fall_end, continued, events)
        self.assertEqual(len(self._infusions(events)), 1)

    def test_original_gap_preserves_background_until_new_observed_block(self):
        count = int(BY_KEY["curve_original_samples"].minimum)
        self.parameters = detection_parameters(
            curve_original_samples=count,
            curve_original_gap_humidity_upper_seconds=self.step / 4,
        )
        self.p = self.parameters.values
        self.side = count
        self.width = (2 * count + 2) * self.step
        points, shapes, _, _ = self._scenario(merged=False)
        cadence = self.step / 4
        binding_second = self.begin + count * self.step
        turn_second = binding_second - count * cadence
        turn_humidity = self.baseline + (self.peak - self.baseline) / 10
        # Dense originals would put both K-windows on the same linear rise
        # before the declared form becomes available. Keep the existing
        # baseline, peak and fall, but supply a real positive slope turn at
        # that boundary instead of relying on a declared RH direction alone.
        points.insert(2, (turn_second, turn_humidity))
        before_slope = (turn_humidity - self.baseline) / (turn_second - self.begin)
        after_slope = (self.peak - turn_humidity) / (
            self.begin + self.width - turn_second
        )
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
            for tick in range(int(first_background / cadence) + 1):
                second = tick * cadence
                self._send(detector, second, self._value(points, second), events)
            self.assertEqual(len(self._infusions(events)), 1)
            original = self._infusions(events)[0]
            self.assertEqual(original.effective_at, self._at(self.begin))
            component = detector._infusion_episode["original_component"]
            self.assertEqual(component["received_at"], self._at(binding_second))
            self.assertEqual({name for name, _ in component["humidity_kinks"]},
                             {Position.UPPER.value})
            for name, kink in component["humidity_kinks"]:
                with self.subTest(position=name):
                    self.assertEqual(kink.factor, self.p["curve_original_kink_factor"])
                    self.assertEqual(
                        tuple(stamp for stamp, _ in kink.window.previous),
                        tuple(self._at(turn_second - (count - 1 - index) * cadence)
                              for index in range(count)),
                    )
                    self.assertEqual(
                        tuple(stamp for stamp, _ in kink.window.current),
                        tuple(self._at(turn_second + (index + 1) * cadence)
                              for index in range(count)),
                    )
                    self.assertAlmostEqual(kink.window.b, before_slope)
                    self.assertAlmostEqual(kink.window.v, after_slope)
                    self.assertGreater(kink.window.b,
                                       self.p["curve_original_significance"] * kink.window.e_minus)
                    self.assertGreater(kink.delta, kink.tolerance)
                    self.assertGreater(kink.effective_change, 0)
            self.assertEqual(detector._infusion_episode["confirmation_basis"], "after_fall")
            self.assertTrue(component["strong"])
            self.assertTrue(component["qualified"])
            self.assertTrue(component["fall_confirmed"])
            bound = dict(component["binding_windows"])
            for name, pair in component["fall_windows"]:
                self.assertGreater(pair.temperature.newest_at, bound[name].temperature.newest_at)
                self.assertGreater(pair.humidity.newest_at, bound[name].humidity.newest_at)
            bound_at = detector.infusion_episode.bound_at
            reference = detector._infusion_episode["original_background"]
            self.assertIsNotNone(reference)
            self.assertEqual(detector.infusion_episode.phase, "active")
            # An unavailable prefix supplies no counterevidence. A later
            # complete observed block may prove the current end, never an
            # earlier end inside the unobserved interval.
            self._send(
                detector, first_background + 3 * self.step / 8, self.level, events,
                omit=((Position.UPPER, Quantity.HUMIDITY),),
            )
            self.assertEqual(detector._infusion_episode["original_background"], reference)
            recovery_step = self.step / (2 * (2 * count - 1))
            for number in range(2 * count):
                second = first_background + self.step / 2 + number * recovery_step
                self._send(detector, second, self.level, events)
            endpoint = detector._original_window_cache[Position.UPPER, Quantity.HUMIDITY][1]
            self.assertIsNotNone(endpoint)
            self.assertEqual(endpoint.start_at, self._at(first_background + self.step / 2))
            self.assertEqual(endpoint.newest_at, self._at(first_background + self.step))
            self.assertEqual(detector.infusion_episode.phase, "ended")
            self.assertEqual(detector.infusion_episode.level_end_at,
                             self._at(first_background + self.step))
            self._send(detector, first_background + self.step + recovery_step,
                       self.level, events)
            events.extend(detector.advance(self._at(first_background + 2 * self.step), enabled=True))
        self.assertEqual(detector.infusion_episode.phase, "ended")
        self.assertEqual(detector.infusion_episode.bound_at, bound_at)
        self.assertIs(detector.infusion_episode.confirmed_detection, original)
        self.assertEqual(self._infusions(events), [original])

    def test_native_infusion_end_uses_rh_while_temperature_messages_pause(self):
        points, _, next_rise, next_fall = self._scenario(merged=False)
        fall = self.begin + 2 * self.width
        # Keep the first background and the resumed T/RH baseline separate
        # from the second rise; the pause concerns only the first RH ending.
        shift = 4 * self.side * self.step
        points = [(second + shift if second >= next_rise else second, value)
                  for second, value in points]
        next_rise += shift
        next_fall += shift
        results = []
        for paused in (False, True):
            with self.subTest(temperature_paused=paused):
                detector = Detector(self.parameters, T0)
                events = []
                self._feed(detector, fall, points, events)
                self.assertEqual(len(self._infusions(events)), 1)
                first = self._infusions(events)[0]
                for second in range(fall + self.step, fall + (3 * self.side + 1) * self.step, self.step):
                    self._send(
                        detector, second, self._value(points, second), events,
                        omit=tuple((position, Quantity.TEMPERATURE) for position in Position)
                        if paused else (),
                    )
                self.assertEqual(detector.infusion_episode.phase, "ended")
                self.assertIs(detector.infusion_episode.confirmed_detection, first)
                results.append(detector.infusion_episode.level_end_at)
                # Resuming T and a genuinely separate rise still creates a
                # second event rather than reopening the first episode.
                self._feed(detector, next_fall + self.side * self.step, points, events)
                self.assertEqual(len(self._infusions(events)), 2)
                self.assertGreater(self._infusions(events)[1].effective_at, results[-1])
        self.assertEqual(len(results), 2)
        if len(results) == 2:
            self.assertEqual(results[0], results[1])

    def test_pending_infusion_keeps_bound_evidence_across_empirical_gap(self):
        points, shapes, _, _ = self._scenario(merged=False)
        detector = Detector(self.parameters, T0, (Position.UPPER,))
        events = []
        first = self.begin + (self.side + 1) * self.step
        gap = ceil(max(self.p[f"curve_original_gap_{quantity.value}_upper_seconds"]
                       for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY))) + self.step
        # Continue a real rise after the unavailable interval. Its evidence is
        # still the same bound component; neither the gap nor ticks confirm it.
        end = first + gap + (2 * self.side + 2) * self.step
        points = [(0, self.baseline), (self.begin, self.baseline),
                  (end + self.width, self.peak)]
        shapes = [self._shape("rise", self.begin, end + self.width)]
        with self._patch_math(shapes):
            self._feed(detector, first, points, events)
            component = detector._original_infusion_candidate
            owner = detector.infusion_episode
            self.assertIsNotNone(component)
            self.assertEqual(owner.phase, "pending")
            events.extend(detector.advance(self._at(first + gap - self.step), enabled=True))
            self.assertIs(detector._original_infusion_candidate, component)
            self._feed(detector, end, points, events)
            self.assertIs(detector._original_infusion_candidate, component)
            self.assertEqual(detector.infusion_episode.bound_at, owner.bound_at)
            self.assertEqual(self._infusions(events), [])

    def test_new_observed_pulse_after_gap_uses_its_own_beginning(self):
        points, shapes, next_rise, next_fall = self._scenario(merged=False)
        detector = Detector(self.parameters, T0, (Position.UPPER,))
        events = []
        first = self.begin + (self.side + 1) * self.step
        resume = next_rise - (2 * self.side + 1) * self.step
        self.assertGreater(resume - first, max(
            self.p[f"curve_original_gap_{quantity.value}_upper_seconds"]
            for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY)
        ))
        # Omit reports from the existing first pulse's unobserved peak/tail.
        # The second pulse and its preceding plateau keep their original inputs.
        with self._patch_math(shapes):
            self._feed(detector, first, points, events)
            old = detector.infusion_episode
            component = detector._position_state(Position.UPPER)["infusion_candidate"]
            context = detector._original_context()
            self.assertIsNotNone(component)
            self.assertEqual(old.phase, "pending")
            self.assertEqual(self._infusions(events), [])
            events.extend(detector.advance(self._at(resume - self.step), enabled=True))
            self.assertIs(detector._position_state(Position.UPPER)["infusion_candidate"], component)
            self.assertEqual(detector.infusion_episode.bound_at, old.bound_at)
            self.assertEqual(self._infusions(events), [])
            self._feed(detector, resume, points, events)
            self.assertIs(detector._position_state(Position.UPPER)["infusion_candidate"], component)
            self.assertEqual(self._infusions(events), [])
            support = detector._original_time_support_cache[
                Position.UPPER, Quantity.HUMIDITY
            ][2]
            self.assertFalse(support.covers(self._at(first), self._at(resume)))
            self._feed(detector, next_fall, points, events)
        infusions = self._infusions(events)
        self.assertEqual(len(infusions), 1)
        self.assertEqual(infusions[0].effective_at, self._at(next_rise))
        self.assertGreater(infusions[0].trace_at, self._at(next_rise))
        self.assertEqual(infusions[0].channels, (Position.UPPER.value,))
        self.assertEqual(detector._original_context(), context)
        self.assertNotEqual(detector.infusion_episode.bound_at, old.bound_at)
        current = detector._position_state(Position.UPPER)["infusion_candidate"]
        self.assertIsNot(current, component)
        self.assertTrue(all(pair.temperature.start_at >= self._at(resume)
                            and pair.humidity.start_at >= self._at(resume)
                            for _, pair in current["binding_windows"]))

    def test_pending_rise_is_not_expired_by_ticks_without_counterevidence(self):
        points, shapes, _, _ = self._scenario(merged=False)
        detector = Detector(self.parameters, T0, (Position.UPPER,))
        events = []
        with self._patch_math(shapes):
            self._feed(detector, self.begin + (self.side + 1) * self.step, points, events)
            episode = detector.infusion_episode
            component = detector._original_infusion_candidate
            self.assertEqual(episode.phase, "pending")
            # Stay inside the separate sensor availability period, but beyond
            # the former 60-second candidate lifetime. Ticks supply no evidence.
            later = self.begin + self.p["curve_window_seconds"] + 2 * self.step
            events.extend(detector.advance(self._at(later), enabled=True))
            self.assertEqual(detector.infusion_episode.bound_at, episode.bound_at)
            self.assertIs(detector._original_infusion_candidate, component)
            self.assertEqual(self._infusions(events), [])

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
        self.assertEqual(detector._infusion_episode["confirmation_basis"], "after_fall")
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

    def test_native_fresh_rise_confirms_once_before_both_position_peaks(self):
        points, _, _, _ = self._scenario(merged=False)
        # Both positions deliberately use the same existing fixture curve.
        # This is a causal lifecycle case, not an R07 sensor-specific replay.
        peaks = {position: self._at(self.begin + self.width)
                 for position in (Position.UPPER, Position.LOWER)}
        detector = Detector(self.parameters, T0)
        controller = Controller(self.parameters)
        controller.set_temperature(self.temperature, T0)
        controller.begin_session("s", T0)
        controller.process(event("initial-open", Kind.DOOR_OPEN, 0))
        controller.process(event("initial-close", Kind.DOOR_CLOSE, 0))
        events = []
        self._feed(detector, self.begin, points, events, controller=controller)
        self.assertEqual(self._infusions(events), [])
        second = self.begin + self.step
        while second <= self.begin + self.width:
            self._send(detector, second, self._value(points, second), events,
                       controller=controller)
            if self._infusions(events):
                break
            second += self.step
        self.assertEqual(len(self._infusions(events)), 1)
        infusion = self._infusions(events)[0]
        self.assertEqual(infusion.detected_at, self._at(second))
        self.assertEqual(infusion.trace_at, infusion.detected_at)
        for position, peak in peaks.items():
            with self.subTest(position=position):
                self.assertLess(infusion.detected_at, peak)
        self.assertLessEqual(infusion.effective_at, infusion.trace_at)
        episode = detector._infusion_episode
        self.assertEqual(episode["confirmation_basis"], "early_rise")
        component = episode["original_component"]
        self.assertFalse(component["fall_ready"])
        self.assertFalse(component["fall_confirmed"])
        native = original_contrasts(
            dict(component["early_confirmation_windows"]),
            expected_positions=(Position.UPPER.value,),
        )
        self.assertIsNotNone(native)
        self.assertGreater(native.rise, self.p["curve_original_new_infusion_contrast"])
        self.assertGreater(native.humidity_rise_change,
                           self.p["curve_original_significance"])
        bound = {name: (temperature, humidity)
                 for name, temperature, humidity in component["newest"]}
        for name, pair in native.positions:
            self.assertGreater(pair.temperature.newest_at, bound[name][0])
            self.assertGreater(pair.humidity.newest_at, bound[name][1])

            for quantity in (pair.temperature, pair.humidity):
                self.assertLessEqual(quantity.newest_at, infusion.trace_at)
            state = component["water_state"][Position(name)]
            self.assertGreater(state["maximum"].lower_density, state["base"].upper_density)
            for witness in (state["base"], state["maximum"]):
                self.assertLessEqual(witness.available_at, infusion.trace_at)
        self._feed(detector, self.begin + 2 * self.width + (self.side + 1) * self.step,
                   points, events, controller=controller)
        self.assertEqual(self._infusions(events), [infusion])
        self.assertIs(detector.infusion_episode.confirmed_detection, infusion)
        gang = controller.session.timeline.active
        self.assertIsNotNone(gang)
        self.assertEqual(len(gang.infusion_events), 1)
        self.assertEqual(gang.infusion_events[0].booking_at, infusion.trace_at)
        consumed = [item for item in controller.consumer_events if item.kind == "infusion"]
        self.assertEqual(len(consumed), 1)
        self.assertEqual(consumed[0].received_at, infusion.detected_at)

    def test_real_rh_negative_kink_can_open_a_door_inside_an_active_infusion(self):
        points, _, _, _ = self._scenario(merged=False)
        peak = self.begin + self.width
        fall = peak + self.width
        detector = Detector(self.parameters, T0)
        events = []
        self._feed(detector, peak, points, events)
        self.assertEqual(len(self._infusions(events)), 1)
        active = detector.infusion_episode
        self.assertEqual(active.phase, "active")
        temperatures = [(0, self.temperature), (peak, self.temperature),
                        (fall, trace(85)[0])]
        self._feed(detector, fall, points, events, temperatures=temperatures)
        self.assertEqual([item.kind for item in events].count(Kind.DOOR_OPEN), 1)
        self.assertTrue(detector.open)
        self.assertIsNone(detector.infusion_episode)
        candidate = detector.door_episode["original_open"]
        self.assertGreater(candidate["humidity_fall_onset"],
                           self.p["curve_original_significance"])
        self.assertGreater(candidate["received_at"], self._at(peak))
        self.assertGreater(candidate["confirmation_received_at"], candidate["received_at"])

    def test_early_confirmation_q_correction_is_an_existing_interface_special_case(self):
        """Historical correction contract; no observed HA sensor path is claimed."""
        points, _, _, _ = self._scenario(merged=False)
        detector = Detector(self.parameters, T0)
        events = []
        # Optional onset models are absent. R, Q and every water pair remain
        # native; this isolates the stored original-evidence correction API.
        with self._patch_math([], forms=original_contrasts):
            peak = self.begin + self.width
            self._feed(detector, self.begin, points, events)
            for second in range(self.begin + self.step, peak + self.step, self.step):
                self._send(detector, second, self._value(points, second), events)
                if self._infusions(events):
                    break
            self.assertEqual(len(self._infusions(events)), 1)
            infusion = self._infusions(events)[0]
            self.assertLess(infusion.trace_at, self._at(peak))
            episode = detector._infusion_episode
            self.assertEqual(episode["confirmation_basis"], "early_rise")
            component = episode["original_component"]
            confirming = dict(component["early_confirmation_windows"])
            expected = (Position.UPPER.value,)
            old = original_contrasts(confirming, expected_positions=expected)
            self.assertIsNotNone(old)
            self.assertGreater(old.humidity_rise_change,
                               self.p["curve_original_significance"])
            humidity = confirming["upper"].humidity
            binding = dict(component["binding_windows"])
            binding_humidity = binding["upper"].humidity
            # Correct the last actual preceding-block original, which also
            # closes the immutable binding block. Its positive OLS weight
            # raises b to v: R stays positive while Q becomes zero. The bound
            # positive knee becomes stronger rather than being removed.
            corrected_at, previous_value = humidity.previous[-1]
            self.assertEqual(corrected_at, binding_humidity.current[-1][0])
            seconds = [(stamp - humidity.previous[0][0]).total_seconds()
                       for stamp, _ in humidity.previous]
            center = sum(seconds) / len(seconds)
            weight = (seconds[-1] - center) / sum((second - center) ** 2
                                                 for second in seconds)
            self.assertGreater(weight, 0)
            correction = previous_value + (humidity.v - humidity.b) / weight
            self.assertGreater(correction, previous_value)
            self.assertIn(corrected_at, {
                stamp for _, pair in component["rise_windows"]
                for stamp, _ in pair.humidity.previous + pair.humidity.current
            })
            water = {position: (state["base"], state["maximum"])
                     for position, state in component["water_state"].items()}
            self.assertTrue(all(
                corrected_at != witness.humidity_sample[0]
                for witnesses in water.values() for witness in witnesses
            ))
            held_rise = component["rise_score"]
            self.assertGreater(held_rise, component["threshold"])
            corrected = original_window(
                tuple((stamp, correction if stamp == corrected_at else value)
                      for stamp, value in humidity.previous + humidity.current),
                count=self.side, scale=humidity.scale, at=humidity.newest_at,
                max_gap_seconds=self.p["curve_original_gap_humidity_upper_seconds"],
            )
            self.assertIsNotNone(corrected)
            self.assertAlmostEqual(corrected.b, humidity.v)
            self.assertEqual(corrected.v, humidity.v)
            proof = original_contrasts({
                **confirming, "upper": replace(confirming["upper"], humidity=corrected),
            }, expected_positions=expected)
            self.assertIsNotNone(proof)
            self.assertEqual(proof.rise, old.rise)
            self.assertGreater(proof.rise, self.p["curve_original_new_infusion_contrast"])
            self.assertLessEqual(proof.humidity_rise_change,
                                 self.p["curve_original_significance"])
            corrected_binding = original_window(
                tuple((stamp, correction if stamp == corrected_at else value)
                      for stamp, value in binding_humidity.previous + binding_humidity.current),
                count=self.side, scale=binding_humidity.scale, at=binding_humidity.newest_at,
                max_gap_seconds=self.p["curve_original_gap_humidity_upper_seconds"],
            )
            self.assertIsNotNone(corrected_binding)
            binding_proof = original_contrasts({
                **binding, "upper": replace(binding["upper"], humidity=corrected_binding),
            }, expected_positions=expected)
            self.assertGreater(corrected_binding.v, binding_humidity.v)
            self.assertAlmostEqual(corrected_binding.b, binding_humidity.b)
            self.assertGreater(binding_proof.humidity_rise_onset,
                               self.p["curve_original_significance"])
            rise_windows = dict(component["rise_windows"])
            rise_humidity = rise_windows["upper"].humidity
            corrected_rise = original_window(
                tuple((stamp, correction if stamp == corrected_at else value)
                      for stamp, value in rise_humidity.previous + rise_humidity.current),
                count=self.side, scale=rise_humidity.scale, at=rise_humidity.newest_at,
                max_gap_seconds=self.p["curve_original_gap_humidity_upper_seconds"],
            )
            self.assertIsNotNone(corrected_rise)
            rise_proof = original_contrasts({
                **rise_windows, "upper": replace(rise_windows["upper"], humidity=corrected_rise),
            }, expected_positions=expected)
            self.assertIsNotNone(rise_proof)
            self.assertGreater(rise_proof.rise, component["threshold"])
            detector._correct_original_infusion(
                Position.UPPER, Quantity.HUMIDITY, corrected_at, correction,
            )
            self.assertEqual(component["rise_score"], rise_proof.rise)
            self.assertEqual(water, {
                position: (state["base"], state["maximum"])
                for position, state in component["water_state"].items()
            })
            self.assertTrue(all(witness["change"] > 0
                                for witness in episode["water"].values()))
            self.assertIsNone(detector._original_infusion_candidate)
            self.assertIsNone(episode["original_component"])
            self.assertIn(Position.UPPER, episode["interrupted"])
            self.assertIs(detector.infusion_episode.confirmed_detection, infusion)
            self.assertEqual(self._infusions(events), [infusion])

    def test_multiple_peaks_before_level_end_remain_one_infusion(self):
        points, shapes, rise_two, fall_two = self._scenario(merged=True)
        detector = Detector(self.parameters, T0)
        events = []
        self.assertIsNone(detector.infusion_episode)
        with self._patch_math(shapes):
            self._feed(detector, self.begin + (self.side + 1) * self.step, points, events)
            pending = detector.infusion_episode
            self.assertEqual(pending.phase, "pending")
            self.assertEqual(pending.positions, (Position.UPPER,))
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
            level_end = self._declared_flat_end(fall_two)
            self._feed(detector, level_end - self.step, points, events)
            self.assertEqual(detector.infusion_episode.phase, "active")
            self.assertIsNone(detector.infusion_episode.level_end_at)
            self._feed(detector, level_end, points, events)
        self.assertEqual(len(self._infusions(events)), 1)
        self.assertEqual(detector._infusion_episode["phase"], "ended")
        self.assertEqual(detector._infusion_episode["end_basis"], "original_background")
        ended = detector.infusion_episode
        self.assertEqual(ended.phase, "ended")
        self.assertEqual(ended.bound_at, pending.bound_at)
        self.assertIs(ended.confirmed_detection, original)
        self.assertEqual(ended.level_end_at,
                         self._at(level_end))
        self.assertEqual(ended.end_basis, "original_background")
        self.assertEqual(ended.last_rise_at,
                         ((Position.UPPER, self._at(rise_two)),))
        for snapshot, phase in ((pending, "pending"), (active, "active")):
            self.assertEqual(snapshot.phase, phase)
            self.assertIsNone(snapshot.level_end_at)
            self.assertEqual(snapshot.last_rise_at,
                             ((Position.UPPER, self._at(self.begin)),))
        self.assertIsNone(pending.confirmed_detection)
        self.assertIsNone(pending.confirmed_at)

    def test_pending_continuation_keeps_first_original_beginning_until_publication(self):
        points, shapes, rise_two, fall_two = self._scenario(merged=True)
        detector = Detector(self.parameters, T0)
        events, first = [], []

        def defer_publication(episode, *_args):
            # Isolate the pending-owner transition. R14 supplies the independent
            # real-data case; this fixture does not prescribe confirmation math.
            if not first:
                first.append(episode["original_component"])

        with self._patch_math(shapes, forms=original_contrasts):
            with patch.object(detector, "_original_confirm_infusion",
                              side_effect=defer_publication):
                self._feed(detector, rise_two + self.width, points, events)
            self.assertEqual(len(first), 1)
            pending = detector.infusion_episode
            self.assertEqual(pending.phase, "pending")
            self.assertEqual(self._infusions(events), [])
            tail = detector._infusion_episode["original_component"]
            self.assertIsNot(tail, first[0])
            self.assertTrue(tail["qualified"])
            self.assertTrue(tail["continuing"])
            expected = min(witness.onset_at
                           for witness in first[0]["onset_witnesses"].values())
            self.assertLess(expected, min(tail["onsets"].values()))
            self._feed(detector, fall_two, points, events)
        infusions = self._infusions(events)
        self.assertEqual(len(infusions), 1)
        self.assertEqual(infusions[0].effective_at, expected)
        self.assertGreater(infusions[0].detected_at, pending.bound_at)
        self.assertIs(detector.infusion_episode.confirmed_detection, infusions[0])
        self.assertIsNone(detector._infusion_episode["original_start_component"])

    def test_qualified_continuation_accepts_late_historical_witness_and_keeps_delayed_fall(self):
        points, shapes, rise_two, _ = self._scenario(merged=True)
        historical_onset = rise_two
        second_peak = (
            historical_onset + self.p["curve_window_seconds"]
            - (self.side - 2) * self.step
        )
        second_fall = second_peak + self.width
        points[-2:] = [(second_peak, self.peak), (second_fall, self.level)]
        shapes[2:] = [self._shape("decay", second_peak, second_fall)]
        gap = max(self.p[f"curve_original_gap_humidity_{position.value}_seconds"]
                  for position in (Position.UPPER, Position.LOWER))
        reveal_at = rise_two + (self.side + ceil(gap / self.step) + 2) * self.step
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
            self.assertEqual(tuple(component["onset_witnesses"]), (Position.UPPER,))
            for position in component["onset_witnesses"]:
                self.assertIn(self._at(historical_onset), {
                    stamp for stamp, _ in
                    detector._curve_series[position, Quantity.HUMIDITY]["points"]
                })
                witness = component["onset_witnesses"][position]
                first_right = tuple(point for point in witness.original_points
                                    if point[0] > witness.onset_at)[:self.side]
                self.assertEqual(len(first_right), self.side)
                self.assertGreater(
                    (self._at(reveal_at + self.step) - first_right[-1][0]).total_seconds(),
                    self.p[f"curve_original_gap_humidity_{position.value}_seconds"],
                )
                self.assertTrue(all(
                    (right[0] - left[0]).total_seconds()
                    <= self.p[f"curve_original_gap_humidity_{position.value}_seconds"]
                    for left, right in zip(witness.original_points, witness.original_points[1:])
                ))
                current = detector._original_window_cache[position, Quantity.HUMIDITY][1]
                originals = dict(witness.original_points)
                self.assertTrue(all(originals.get(stamp) == value
                                    for stamp, value in current.current))
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
            self._feed(detector, self._declared_flat_end(second_fall), points, events)
        ended = detector.infusion_episode
        self.assertEqual(ended.phase, "ended")
        self.assertEqual(ended.end_basis, "original_background")
        self.assertGreater(ended.level_end_at, self._at(fall_at))
        self.assertEqual(ended.bound_at, active.bound_at)
        self.assertIs(ended.confirmed_detection, original)
        self.assertEqual(self._infusions(events), [original])
        self.assertEqual(active.phase, "active")
        self.assertIsNone(active.level_end_at)

    def test_upper_peak_and_end_keep_one_episode_with_a_different_lower_tail(self):
        first_peak = self.begin + self.width
        first_fall = first_peak + self.width
        upper_rise = first_fall + max(1, self.side - 2) * self.step
        upper_peak = upper_rise + self.width
        upper_fall = upper_peak + self.width
        settled = self._declared_flat_end(upper_fall)
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
                    self.assertEqual(first_episode.positions, (Position.UPPER,))
                    self.assertNotIn("lower", dict(first_component["fall_windows"]))
                    self.assertNotIn(Position.LOWER, first_component["peaks"])
                    first_gang = controller.session.timeline.active
                    self.assertIsNotNone(first_gang)
                    self.assertEqual(len(first_gang.infusion_events), 1)
                    if paused:
                        # The unchanged global gap interrupts the selected
                        # upper background proof and preserves its identity.
                        self._send(detector, first_fall, None, events, controller=controller)
                        interrupted = detector.infusion_episode
                        self.assertEqual(interrupted.interrupted_positions, (Position.UPPER,))
                        self.assertEqual(interrupted.bound_at, first_episode.bound_at)
                        self.assertEqual(first_episode.interrupted_positions, ())
                    self._feed(detector, upper_peak, points, events, controller=controller)
                    episode = detector._infusion_episode
                    self.assertEqual(episode["phase"], "active")
                    self.assertIsNone(episode["level_end_at"])
                    self.assertIsNone(episode["original_background"])
                    self.assertEqual(len(self._infusions(events)), 1)
                    self.assertEqual(episode["onset_at"], self._at(self.begin))
                    lower_level = detector._original_window_cache[
                        Position.LOWER, Quantity.HUMIDITY
                    ][1]
                    self.assertTrue(all(value == self.level / 2
                                        for _, value in lower_level.current))
                    self.assertEqual(len(controller.session.timeline.active.infusion_events), 1)
                    self.assertEqual(len([item for item in controller.consumer_events
                                          if item.kind == "infusion"]), 1)
                    # The selected upper tail needs its own fresh original
                    # background before the episode can end.
                    self._feed(detector, settled - self.step, points, events, controller=controller)
                    self.assertEqual(detector.infusion_episode.phase, "active")
                    self.assertIsNone(detector.infusion_episode.level_end_at)
                    self._feed(detector, settled, points, events, controller=controller)
                    episode = detector._infusion_episode
                    self.assertEqual(episode["phase"], "ended")
                    self.assertEqual(episode["end_basis"], "original_background")
                    self.assertGreater(episode["level_end_at"], self._at(upper_peak))
                    end_windows = dict(episode["original_end_windows"])
                    self.assertEqual(tuple(end_windows), ("upper",))
                    for name, pair in end_windows.items():
                        expected = self.level if name == "upper" else self.level / 2
                        self.assertTrue(all(value == expected for _, value in pair.current))
                        self.assertGreater(pair.newest_at, self._at(upper_peak))
                    self.assertEqual(len(self._infusions(events)), 1)
                    ended_episode = detector.infusion_episode
                    self._feed(detector, next_fall, points, events, controller=controller)
                infusions = self._infusions(events)
                self.assertEqual(len(infusions), 2)
                self.assertEqual([item.effective_at for item in infusions],
                                 [self._at(self.begin), self._at(next_rise)])
                self.assertTrue(all(item.channels == ("upper",) for item in infusions))
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
                    self.assertEqual(interrupted.interrupted_positions, (Position.UPPER,))
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

    def test_native_rise_current_block_after_end_allows_overlapping_reference(self):
        for separated in (True, False):
            with self.subTest(binding_support_after_level_end=separated):
                points, native_shapes, next_rise, next_fall = self._scenario(merged=False)
                ended = self._declared_flat_end(self.begin + 2 * self.width)
                if not separated:
                    next_rise = ended + self.step
                    next_peak = next_rise + self.width
                    next_fall = next_peak + self.width
                    points = points[:4] + [(next_rise, self.level), (next_peak, self.peak),
                                          (next_fall, self.level)]
                    native_shapes = native_shapes[:2] + [
                        self._shape("rise", next_rise, next_peak),
                        self._shape("decay", next_peak, next_fall),
                    ]
                detector = Detector(self.parameters, T0)
                events = []

                def forms(windows, **kwargs):
                    return self._original_forms(native_shapes, windows, **kwargs)

                # Native original forms carry both pulses. No optional curve
                # witness supplies either episode's onset or assignment.
                with self._patch_math([], forms=forms):
                    self._feed(detector, next_rise - self.step, points, events)
                    first = detector.infusion_episode
                    self.assertEqual(first.phase, "ended")
                    self.assertEqual(first.level_end_at, self._at(ended))
                    self.assertEqual(len(self._infusions(events)), 1)
                    self._feed(detector, next_rise + self.side * self.step, points, events)
                    component = detector._original_infusion_candidate
                    self.assertEqual(component["onset_witnesses"], {})
                    self.assertEqual(component["assignment"], "new")
                    self.assertTrue(all(pair.humidity.current[0][0] > first.level_end_at
                                        for _, pair in component["binding_windows"]))
                    self.assertEqual(all(
                        pair.humidity.start_at > first.level_end_at
                        for _, pair in component["binding_windows"]
                    ), separated)
                    self._feed(detector, next_fall, points, events)
                infusions = self._infusions(events)
                self.assertEqual(len(infusions), 2)
                self.assertGreater(infusions[1].effective_at, first.level_end_at)
                self.assertNotEqual(detector.infusion_episode.bound_at, first.bound_at)
                self.assertIs(detector.infusion_episode.confirmed_detection, infusions[1])

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
            status = detector._position_state(Position.UPPER)["infusion_candidate"]
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
            self.assertFalse(detector._position_state(Position.UPPER)["infusion_candidate"]["fall_confirmed"])
            self.assertNotIn("fall_windows", detector._position_state(Position.UPPER)["infusion_candidate"])
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
            self.assertEqual(witness, (("upper", self._at(first)),))
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
            # The restored original is still stamped first. Neither the tick
            # nor the two same-stamp reports supply a new RH original.
            self.assertEqual(detector._infusion_episode["original_background"], witness)
            # Finish the same flat tail. Its preceding K-block must also clear
            # the genuine fall/plateau knee before a fresh end block can count.
            resumed = first + 4 * self.step
            level_end = resumed + (2 * self.side - 1) * self.step
            for second in range(first + 5 * self.step, level_end, self.step):
                self._send(detector, second, self.level, events)
                self.assertEqual(detector.infusion_episode.phase, "active")
                self.assertIsNone(detector.infusion_episode.level_end_at)
            self._send(detector, level_end, self.level, events)
        self.assertEqual(detector._infusion_episode["phase"], "ended")
        self.assertEqual(len(self._infusions(events)), 1)

    def test_source_gap_can_recover_a_new_level_without_inventing_a_decay(self):
        first_peak = self.begin + self.width
        gap = first_peak + (self.side + 2) * self.step
        recovered = gap + self.step
        # Source replacement starts an empty original history: 2K values
        # supply the first complete pair of windows, then K fresh RH values.
        settled = recovered + (3 * self.side - 1) * self.step
        next_rise = settled + self.p["infusion_window_seconds"] + self.step
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
            self._feed(detector, settled - self.step, points, events,
                       humidity_source="sensor.recovered_humidity")
            self.assertEqual(detector.infusion_episode.phase, "active")
            self.assertIsNone(detector.infusion_episode.level_end_at)
            self._feed(detector, settled, points, events,
                       humidity_source="sensor.recovered_humidity")
            self.assertEqual(detector._infusion_episode["phase"], "ended")
            self.assertEqual(detector._infusion_episode["end_basis"], "original_background")
            self.assertEqual(detector._infusion_episode["decays"], {})
            self.assertTrue(detector.infusion_episode.interrupted_positions)
            self.assertTrue(all(
                value == self.level for _, pair in
                detector._infusion_episode["original_end_windows"]
                for _, value in pair.current
            ))
            self.assertEqual(len(self._infusions(events)), 1)
            self._feed(detector, next_fall, points, events,
                       humidity_source="sensor.recovered_humidity")
        self.assertEqual(len(self._infusions(events)), 2)
        self.assertTrue(any(
            row["decisions"]["infusion_background"]["result"] is True
            for row in observed
        ))

    def test_ols_opening_retains_bound_original_onset_without_backdating_confirmation(self):
        flank_seconds = self.p["curve_min_side_samples"] * self.step
        ready = self.begin + flank_seconds
        # Two originals first make the suffix mathematically evaluable.
        # Public confirmation additionally requires a complete fresh K-block
        # for every bound T/RH role; the retained onset remains independent.
        confirmed = ready + self.side * self.step
        recognized_at = self._at(confirmed + 5 * self.step)
        end = max(self.begin + self.width, confirmed)
        fall_seconds = end - self.begin
        initial_t, falling_t = trace(70)[0], trace(70 + self.step)[0]
        # Spread the existing fixture's first loss over the configured local
        # model flank, leaving an observable onset before OLS confirmation.
        temperatures = [(0, self.temperature), (self.begin, self.temperature),
                        (end, self.temperature
                         + (falling_t - initial_t) * fall_seconds / flank_seconds)]
        # Consume the RH noise scales and this fixture's exact time geometry.
        # The chosen rate first resolves the RH kink at ready. The following
        # original suffix must independently prove the shared T/RH loss.
        unit_fall = tuple((self._at(second), -max(0, second - self.begin))
                          for second in range(0, ready + self.step, self.step))
        rate_boundaries = {}
        for at in (ready - self.step, ready):
            rate_boundaries[at] = []
            for position in (Position.UPPER, Position.LOWER):
                window = original_window(
                    unit_fall, count=self.side,
                    scale=self.p[f"curve_original_noise_humidity_{position.value}"],
                    max_gap_seconds=self.p[f"curve_original_gap_humidity_{position.value}_seconds"],
                    at=self._at(at),
                )
                self.assertIsNotNone(window)
                unit_onset = min(-window.v / window.e,
                                 -(window.v - window.b) / (window.e + window.e_minus))
                rate_boundaries[at].append(self.p["curve_original_significance"] / unit_onset)
        lower, upper = max(rate_boundaries[ready]), min(rate_boundaries[ready - self.step])
        self.assertLess(lower, upper)
        humidity_rate = (lower + upper) / 2
        humidities = [(0, self.baseline), (self.begin, self.baseline),
                      (end, self.baseline - humidity_rate * fall_seconds)]
        shapes = [
            self._shape("open", self.begin, end, Quantity.TEMPERATURE),
            self._shape("open", self.begin, end),
        ]
        prepared = Detector(self.parameters, T0)
        prepared.report_heating(True, T0)
        # Both physical inputs remain present; only the leading position
        # supplies the bound opening and its optional curve witnesses.
        positions = (Position.UPPER,)
        quantities = (Quantity.TEMPERATURE, Quantity.HUMIDITY)
        mixed_key = ("door_open", positions, quantities)
        humidity_key = ("door_open", positions, (Quantity.HUMIDITY,))
        thermal_key = ("door_open_heating", positions, (Quantity.TEMPERATURE,))
        events = []
        # The declared local models supply onset attribution only. OLS sees
        # the actual small falls, which have not confirmed the opening yet.
        with self._patch_math(shapes, forms=original_contrasts):
            self._feed(prepared, ready, humidities, events, temperatures=temperatures)
            self.assertFalse(prepared.open)
            self.assertNotIn(Kind.DOOR_OPEN, [item.kind for item in events])
            native = prepared.door_episode["original_open"]
            self.assertEqual(native["received_at"], self._at(ready))
            self.assertGreater(native["humidity_fall_onset"], self.p["curve_original_significance"])
            bound_onsets = native["onset_witnesses"]
            self.assertEqual({key[1:] for key, _ in bound_onsets},
                             {(position, Quantity.HUMIDITY) for position in positions})
            self.assertEqual({witness.onset_at for _, witness in bound_onsets},
                             {self._at(self.begin)})
            self.assertIn(thermal_key, prepared._curve_proposals)
            self.assertTrue(prepared._remember_curve_proposal(
                "door_open", positions, quantities, floor=prepared._curve_closed_since,
                context=(prepared._curve_door_context, prepared._curve_closed_since),
                observed_at=self._at(ready),
            ))
            self.assertIn(mixed_key, prepared._curve_proposals)
            # Later optional models cannot replace the already bound onset.
            # Real original T/RH inputs still have to complete confirmation.
            shapes.pop()
            for case in ("complete_thermal", "incomplete_thermal",
                         "ambiguous_mixed", "controller_rejected_mixed"):
                with self.subTest(case=case):
                    shapes[0]["until"] = ready if case == "incomplete_thermal" else end
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
                            bound_candidates=bound_onsets,
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
                    native = detector.door_episode["original_open"]
                    self._feed(detector, confirmed - self.step, humidities, found,
                               temperatures=temperatures)
                    self.assertFalse(detector.open)
                    self.assertNotIn(Kind.DOOR_OPEN, [item.kind for item in found])
                    self.assertNotIn("lowering_witness", native)
                    self.assertFalse(detector.diagnostic["original_forms"][
                        "complete_fresh_original_block"
                    ]["result"])
                    bound = dict(native["windows"])
                    backgrounds = dict(native["humidity_kinks"])
                    for name, pair in bound.items():
                        for quantity, values in ((Quantity.TEMPERATURE, temperatures),
                                                 (Quantity.HUMIDITY, humidities)):
                            originals = tuple((self._at(second), self._value(values, second))
                                              for second in range(0, confirmed + self.step, self.step))
                            reference = getattr(pair, quantity.value)
                            background = backgrounds[name] if quantity == Quantity.HUMIDITY else None
                            first = reference.newest_at + timedelta(seconds=self.step)
                            second = first + timedelta(seconds=self.step)
                            self.assertIsNone(original_decline_evidence(
                                tuple(point for point in originals if point[0] <= first),
                                reference, background=background,
                            ))
                            first_evaluable = original_decline_evidence(
                                tuple(point for point in originals if point[0] <= second),
                                reference, background=background,
                            )
                            self.assertIsNotNone(first_evaluable)
                            self.assertEqual(len(first_evaluable.points), 2)
                            self.assertGreater(first_evaluable.level_loss,
                                               self.p["curve_original_open_contrast"])
                            if quantity == Quantity.TEMPERATURE:
                                self.assertLess(first_evaluable.direction,
                                                -self.p["curve_original_open_contrast"])
                            proof = original_decline_evidence(
                                originals, reference, background=background,
                            )
                            self.assertIsNotNone(proof)
                            self.assertEqual(len(proof.points), self.side)
                            self.assertGreater(proof.level_loss,
                                               self.p["curve_original_open_contrast"])
                            if quantity == Quantity.TEMPERATURE:
                                self.assertLess(proof.direction,
                                                -self.p["curve_original_open_contrast"])
                    self._send(
                        detector, confirmed, self._value(humidities, confirmed), found,
                        temperature=self._value(temperatures, confirmed),
                        detected_at=recognized_at,
                    )
                    openings = [item for item in found if item.kind == Kind.DOOR_OPEN]
                    self.assertEqual(len(openings), 1)
                    opening = openings[0]
                    effective_at = self._at(self.begin)
                    self.assertEqual(opening.effective_at, effective_at)
                    self.assertEqual(opening.trace_at, self._at(confirmed))
                    self.assertEqual(opening.detected_at, recognized_at)
                    self.assertEqual(opening.channels, tuple(position.value for position in positions))
                    self.assertTrue(detector.open)
                    self.assertTrue(detector.door_episode["opening_humidity"])
                    self.assertIs(detector.door_episode["original_open"], native)
                    self.assertEqual(native["onset_witnesses"], bound_onsets)
                    self.assertEqual(native["confirmation_received_at"], self._at(confirmed))
                    self.assertEqual(native["lowering_witness"]["received_at"],
                                     self._at(confirmed))
                    for name, pair in native["confirmation_windows"]:
                        self.assertGreater(pair.temperature.current[0][0],
                                           bound[name].temperature.newest_at)
                        self.assertGreater(pair.humidity.current[0][0],
                                           bound[name].humidity.newest_at)
                        self.assertEqual(len(pair.temperature.current), self.side)
                        self.assertEqual(len(pair.humidity.current), self.side)
                        self.assertEqual(pair.temperature.newest_at, self._at(confirmed))
                        self.assertEqual(pair.humidity.newest_at, self._at(confirmed))
                        self.assertEqual(detector.door_episode["sources"][Position(name)],
                                         (f"sensor.{name}_temperature", f"sensor.{name}_humidity"))
                    self.assertEqual(detector.opened_at, effective_at)
                    self.assertEqual(detector.door_episode["started_at"], effective_at)
                    diagnostic = detector.diagnostic
                    self.assertTrue(diagnostic["decisions"]["door_open"]["result"])
                    self.assertEqual(diagnostic["door_open_onset_attribution"], {
                        "confirmation_basis": "original_ols",
                        "route": "door_open",
                        "quantities": ("humidity",),
                        "thermal_fallback": False,
                        "attribution_basis": "original_open_binding",
                        "bound_original_onsets": tuple(
                            (key[1].value, key[2].value, witness.onset_at, witness.candidate_id)
                            for key, witness in bound_onsets
                        ),
                        "used_proposal": True,
                        "effective_at": effective_at,
                    })
                    proposal = detector._curve_proposal_status[humidity_key]
                    self.assertEqual(proposal["confirmed_at"], recognized_at)
                    self.assertEqual(proposal["consumed_onset_at"], self._at(self.begin))
                    self.assertEqual(proposal["consumed_candidate_ids"],
                                     tuple(witness.candidate_id for _, witness in bound_onsets))

    def test_door_context_assigns_a_new_infusion_to_the_next_real_gang(self):
        from json import dumps

        first_peak = self.begin + self.width
        first_fall = first_peak + self.width
        # Reuse the native opening from
        # test_real_rh_negative_kink_can_open_a_door_inside_an_active_infusion:
        # the first peak turns into the shared RH/T fall while the infusion
        # is still active. A later, smaller second RH turn is not substituted.
        door_start = first_peak
        door_bottom = door_start + self.width
        water_plateau = door_bottom + self.width
        close_start = water_plateau + max(
            self.p["infusion_window_seconds"], self.p["median_seconds"],
        ) + self.side * self.step
        close_end = close_start + self.width
        next_rise = close_end + self.p["infusion_window_seconds"] + self.p["median_seconds"]
        next_peak = next_rise + self.width
        next_fall = next_peak + self.width
        fixture = DetectorTests()
        cold, dry = trace(85)[0], self.level
        # Keep the established two-stage ventilation geometry: RH settles
        # first; water settles later while temperature continues to fall.
        close_temperature = cold + (cold - self.temperature) * (close_start - door_bottom) / self.width
        plateau_temperature = cold + (cold - self.temperature) * (water_plateau - door_bottom) / self.width
        plateau_water = absolute_humidity(plateau_temperature, dry)
        close_humidity = fixture._humidity_for_water(close_temperature, plateau_water)
        closed_humidity = fixture._humidity_for_water(self.temperature, plateau_water)
        next_peak_humidity = closed_humidity + (self.peak - self.level)
        points = [
            (0, self.baseline), (self.begin, self.baseline), (first_peak, self.peak),
            (door_bottom, dry), (water_plateau, dry), (close_start, close_humidity),
            (close_end, closed_humidity), (next_rise, closed_humidity),
            (next_peak, next_peak_humidity), (next_fall, closed_humidity),
        ]
        temperatures = [(0, self.temperature), (door_start, self.temperature),
                        (door_bottom, cold), (close_start, close_temperature),
                        (close_end, self.temperature)]
        points = [(second, fixture._humidity_for_water(
            self._value(temperatures, second), plateau_water,
        ) if water_plateau <= second <= next_rise else self._value(points, second))
            for second in range(0, next_fall + self.step, self.step)]
        shapes = [self._shape("rise", self.begin, first_peak),
                  self._shape("decay", first_peak, first_fall),
                  self._shape("rise", next_rise, next_peak),
                  self._shape("decay", next_peak, next_fall)]
        for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY):
            shapes.append(self._shape("open", door_start, door_bottom, quantity))
        shapes.append(self._shape("close", close_start, close_end, Quantity.TEMPERATURE))
        # The closure only warms the retained air. No water is added before
        # the separately declared second infusion; its RH gain is unchanged.
        for second in range(water_plateau, next_rise + self.step, self.step):
            self.assertAlmostEqual(absolute_humidity(
                self._value(temperatures, second), self._value(points, second),
            ), plateau_water)
        self.assertAlmostEqual(next_peak_humidity - closed_humidity, self.peak - self.level)
        episodes_at_opening, opening_proofs, event_evidence = [], [], []

        def water_witness(witness):
            if witness is None:
                return None
            return {
                "humidity": witness.humidity_sample,
                "temperature_before": witness.temperature_before,
                "temperature_after": witness.temperature_after,
                "lower_density": witness.lower_density,
                "upper_density": witness.upper_density,
                "available_at": witness.available_at,
            }

        def observe(row):
            if any(kind in row["signals"] for kind in (
                Kind.INFUSION, Kind.DOOR_OPEN, Kind.DOOR_CLOSE,
                Kind.VENTILATION, Kind.VENTILATION_COMPLETE,
            )):
                native = row.get("original_infusion", {})
                owner = detector._infusion_episode
                component = owner.get("original_component") if owner is not None else None
                event_evidence.append({
                    "at": row["at"], "signals": row["signals"],
                    "episode": row.get("infusion_episode"),
                    "native": {key: native.get(key) for key in (
                        "binding_received_at", "binding_support", "native_onset_intervals",
                        "assignment", "strong", "water_positive", "water_change",
                        "water_support_end", "confirmation_basis",
                    )},
                    "water": {position.value: {
                        key: water_witness(state[key]) for key in ("base", "maximum")
                    } for position, state in component["water_state"].items()}
                    if component is not None else {},
                })
            if Kind.DOOR_OPEN in row["signals"]:
                episodes_at_opening.append(detector.infusion_episode)
                opening_proofs.append(row["original_forms"]["original_fall_evidence"])

        detector = Detector(self.parameters, T0, observer=observe)
        controller = Controller(self.parameters)
        controller.set_temperature(self.temperature, T0)
        controller.begin_session("s", T0)
        controller.process(event("initial-open", Kind.DOOR_OPEN, 0))
        controller.process(event("initial-close", Kind.DOOR_CLOSE, 0))
        events = []
        # The same immutable raw windows feed kink history and decline
        # evidence. Declared OLS replacements would break their identity.
        with self._patch_math(shapes, forms=original_contrasts):
            self._feed(detector, door_start - self.step, points, events,
                       temperatures=temperatures, controller=controller)
            self.assertEqual(detector._infusion_episode["phase"], "active")
            self.assertEqual(len(controller.session.timeline.active.infusion_events), 1)
            first_episode = detector.infusion_episode
            self._feed(detector, next_fall, points, events,
                       temperatures=temperatures, controller=controller)
        boundaries = {
            "first_rise": self.begin, "first_peak": first_peak, "first_fall": first_fall,
            "door_start": door_start, "door_bottom": door_bottom,
            "water_plateau": water_plateau, "close_start": close_start,
            "close_end": close_end, "next_rise": next_rise,
            "next_peak": next_peak, "next_fall": next_fall,
        }
        diagnostic = dumps({
            "inputs": {name: {
                "second": second,
                "temperature": self._value(temperatures, second),
                "humidity": self._value(points, second),
                "water": absolute_humidity(self._value(temperatures, second),
                                           self._value(points, second)),
            } for name, second in boundaries.items()},
            "events": [{"kind": item.kind, "effective_at": item.effective_at,
                        "trace_at": item.trace_at, "detected_at": item.detected_at}
                       for item in events],
            "evidence": event_evidence,
        }, default=lambda value: (value - T0).total_seconds(), sort_keys=True)
        self.assertEqual(len(self._infusions(events)), 2,
                         msg="DOOR_CONTEXT_INFUSION_EVIDENCE=" + diagnostic)
        self.assertEqual(episodes_at_opening, [None])
        self.assertEqual(len(opening_proofs), 1)
        self.assertEqual(set(opening_proofs[0]), {
            "upper:temperature", "upper:humidity",
        })
        for position in (Position.UPPER,):
            for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY):
                proof = opening_proofs[0][f"{position.value}:{quantity.value}"]
                self.assertTrue(proof["available"])
                self.assertTrue(proof["level_loss"]["result"])
                if quantity == Quantity.TEMPERATURE:
                    self.assertTrue(proof["direction"]["result"])
        self.assertEqual(first_episode.phase, "active")
        self.assertIsNone(first_episode.level_end_at)
        next_episode = detector.infusion_episode
        self.assertNotEqual(next_episode.bound_at, first_episode.bound_at)
        self.assertIs(first_episode.confirmed_detection, self._infusions(events)[0])
        self.assertIs(next_episode.confirmed_detection, self._infusions(events)[1])
        self.assertEqual([item.kind for item in events if item.kind in (
            Kind.DOOR_OPEN, Kind.VENTILATION, Kind.VENTILATION_COMPLETE, Kind.DOOR_CLOSE,
        )], [Kind.DOOR_OPEN, Kind.VENTILATION, Kind.VENTILATION_COMPLETE, Kind.DOOR_CLOSE])
        timeline = controller.session.timeline
        self.assertEqual(len(timeline.completed), 1)
        self.assertIsNotNone(timeline.active)
        self.assertNotEqual(timeline.completed[0].gang_id, timeline.active.gang_id)
        self.assertEqual(len(timeline.completed[0].infusion_events), 1)
        self.assertEqual(len(timeline.active.infusion_events), 1)

    def test_corrected_ended_tail_keeps_the_next_peak_in_the_same_infusion(self):
        points, shapes, _, _ = self._scenario(merged=False)
        ended = self._declared_flat_end(self.begin + 2 * self.width)
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

    def test_old_legacy_rise_cannot_reassign_or_block_a_separated_native_infusion(self):
        points, native_shapes, next_rise, next_fall = self._scenario(merged=False)
        first_peak = self.begin + self.width
        confirmed = first_peak + (self.side + 1) * self.step
        received_at = self._at(confirmed + self.step)
        ended = self._declared_flat_end(self.begin + 2 * self.width)
        late_onset = ended - self.step
        second_peak = native_shapes[2]["until"]
        legacy_shapes = native_shapes[:2]
        detector = Detector(self.parameters, T0, (Position.UPPER,))
        events = []

        def forms(windows, **kwargs):
            return self._original_forms(native_shapes, windows, **kwargs)

        with self._patch_math(legacy_shapes, forms=forms):
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
            self.assertIs(ended_episode.confirmed_detection, original)
            self.assertLess(self._at(late_onset), ended_episode.level_end_at)
            # The supported old curve fit contains current originals, but its
            # first right-hand originals still belong to the ended plateau.
            legacy_shapes.append(self._shape("rise", late_onset, second_peak))
            self._feed(detector, next_rise + (self.side + 1) * self.step, points, events)
            candidate = detector._curve_candidates["infusion", Position.UPPER, Quantity.HUMIDITY]
            self.assertEqual(candidate.onset_at, self._at(late_onset))
            self.assertEqual(detector._curve_status[
                "infusion", Position.UPPER, Quantity.HUMIDITY,
            ]["status"], "supported")
            component = detector._original_infusion_candidate
            self.assertEqual(component["assignment"], "new")
            self.assertFalse(component["continuing"])
            self.assertEqual(component["onset_witnesses"], {})
            self.assertTrue(all(pair.humidity.start_at > ended_episode.level_end_at
                                for _, pair in component["binding_windows"]))
            self.assertNotEqual(detector.infusion_episode.bound_at, ended_episode.bound_at)
            self.assertIsNone(detector.infusion_episode.confirmed_detection)
            self.assertEqual(self._infusions(events), [original])
            # The first native RH edge belongs to this pulse. It supplies
            # the beginning even though the older optional witness is invalid.
            interval = component["onset_intervals"][Position.UPPER]
            self.assertEqual(interval.previous[0], self._at(next_rise))
            self.assertEqual(interval.current[0], self._at(next_rise + self.step))
            self._feed(detector, next_fall, points, events)
        infusions = self._infusions(events)
        self.assertEqual(len(infusions), 2)
        self.assertGreater(infusions[1].effective_at, ended_episode.level_end_at)
        self.assertEqual(infusions[1].effective_at, self._at(next_rise))
        self.assertLess(infusions[1].effective_at, infusions[1].trace_at)
        self.assertNotEqual(detector.infusion_episode.bound_at, ended_episode.bound_at)
        self.assertIs(detector.infusion_episode.confirmed_detection, infusions[1])
        self.assertEqual(ended_episode.phase, "ended")
        self.assertEqual(ended_episode.level_end_at, self._at(ended))
        self.assertEqual(ended_episode.end_basis, "original_background")
