"""Sensor ownership over existing, manifest-bound climate fixture segments.

Private candidate for GitHub-hosted Linux execution only. No detector result,
clock, local assessment or numerical model is mocked. The existing fixture
builder supplies climate endpoints and durations; Parameters supplies policy.
Fault/role variants are input robustness checks, not invented human sessions.
"""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import unittest

import test_original_curve_evidence as fixture_source
from test_foundation import selected_occupancy

from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.detector import Detector
from custom_components.ha_sauna.core.models import Measurement, Position, Quantity
from custom_components.ha_sauna.core.presence import binary_presence
from custom_components.ha_sauna.core.timeline import Event, Kind


class SingleSensorClosingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Reuse fixture loading/checksums and its declared interpolation recipe.
        # Import the module, not a TestCase alias: no inherited duplicate tests.
        cls.climate = fixture_source.OriginalCurveFollowupTests(methodName="runTest")
        cls.climate.setUp()
        cls.parameters = cls.climate.parameters
        cls.step = cls.climate.step
        cls.count = cls.climate.count
        cls.origin = fixture_source.ORIGIN
        cls.fall_start = cls.climate.baseline_ticks
        cls.recovery_start = cls.fall_start + cls.climate.door_ticks
        cls.recovery_end = cls.recovery_start + cls.climate.close_ticks
        cls.end = cls.recovery_end + cls.climate.tail_ticks

    @classmethod
    def at(cls, tick):
        return cls.origin + timedelta(seconds=tick * cls.step)

    @classmethod
    def pair(cls, position, tick):
        offset = 0 if position is Position.UPPER else 2
        first = cls.climate.initial[offset:offset + 2]
        last = cls.climate.exchanged[offset:offset + 2]
        if tick < cls.fall_start:
            return first
        if tick < cls.recovery_start:
            fraction = (tick - cls.fall_start + 1) / cls.climate.door_ticks
            return tuple(a + fraction * (b - a) for a, b in zip(first, last, strict=True))
        fraction = min(1, (tick - cls.recovery_start + 1) / cls.climate.close_ticks)
        return last[0] + fraction * (first[0] - last[0]), last[1]

    @classmethod
    def counter_pair(cls, position, tick):
        """Existing short recovery -> renewed fall -> new full recovery recipe."""
        turn = cls.recovery_start + cls.count - 1
        counter_end = turn + 3 * cls.count
        if tick < turn:
            return cls.pair(position, tick)
        offset = 0 if position is Position.UPPER else 2
        start = cls.pair(position, turn - 1)
        delta = cls.climate.fall_step[offset:offset + 2]
        if tick < counter_end:
            return tuple(v + (tick - turn + 1) * d for v, d in zip(start, delta, strict=True))
        bottom = tuple(v + 3 * cls.count * d for v, d in zip(start, delta, strict=True))
        recovery = cls.climate.recovery_step[offset]
        return bottom[0] + min(tick - counter_end + 1, cls.climate.close_ticks) * recovery, bottom[1]

    def run_inputs(self, *, positions=(Position.UPPER, Position.LOWER),
                   lower="constant", upper_freeze=None, upper_failure=None,
                   stop=None, lower_delay=0, selection_records=None):
        observed = []
        detector = Detector(self.parameters, self.origin, positions=positions, observer=observed.append)
        if selection_records is not None:
            # Pass-through observation of the actual local proofs and central
            # choice. A later prefix in one raster may overwrite the public
            # trace; keep the choice with exactly the proofs it consumed.
            local_proofs = {}
            original_local = detector._original_door_position_evidence
            original_select = detector._select_position

            def observe_local(prefixes, channels, now, decision_at, trace, state):
                result = original_local(
                    prefixes, channels, now, decision_at, trace, state,
                )
                forms = trace.get("original_forms", {})
                local_proofs[channels[0].value] = {
                    "prefix_at": prefixes[-1].received_at,
                    "candidate_received_at": forms.get("candidate_received_at"),
                    "closing_candidate_reset_reason": forms.get(
                        "closing_candidate_reset_reason",
                    ),
                    "temperature_fall_reset_reason": trace.get(
                        "temperature_fall_support", {},
                    ).get(channels[0].value, {}).get("reset_reason"),
                }
                return result

            def observe_selection(route, results, owner, trace):
                selected = original_select(route, results, owner, trace)
                if route == "door_close" and selected is not None:
                    selection_records.append({
                        "at": trace["at"],
                        "selection": deepcopy(trace["position_selection"][route]),
                        "local_proofs": deepcopy(local_proofs),
                    })
                return selected

            detector._original_door_position_evidence = observe_local
            detector._select_position = observe_selection
            self.addCleanup(
                setattr, detector, "_original_door_position_evidence", original_local,
            )
            self.addCleanup(setattr, detector, "_select_position", original_select)
        detector.report_heating(False, self.origin)
        events = []
        stop = self.end if stop is None else stop
        for tick in range(stop):
            for position in positions:
                original_tick = tick
                source_suffix = ""
                if position is Position.UPPER:
                    if upper_freeze is not None:
                        original_tick = min(tick, upper_freeze)
                    values = self.pair(position, original_tick)
                    if upper_failure is not None and tick >= upper_failure:
                        values = (None, None)
                elif lower in ("complete", "counter"):
                    values = (self.counter_pair(position, tick - lower_delay)
                              if lower == "counter" else self.pair(position, tick - lower_delay))
                elif lower == "late_exit":
                    values = self.pair(position, tick - self.climate.door_ticks)
                else:
                    values = self.pair(position, -1)
                    if lower == "unavailable" and tick >= self.recovery_start:
                        values = (None, None)
                    if lower == "source_change" and tick >= self.recovery_start:
                        source_suffix = "_replacement"
                for quantity, value in zip((Quantity.TEMPERATURE, Quantity.HUMIDITY), values, strict=True):
                    report = Measurement(
                        position, quantity, value,
                        "unavailable" if value is None else str(value),
                        f"sensor.followup_{position.value}_{quantity.value}{source_suffix}",
                        self.at(tick), self.at(original_tick),
                    )
                    self.assertTrue(detector.accept(report))
                    if position is Position.LOWER and lower == "correction" and tick >= self.recovery_start:
                        column = 0 if quantity is Quantity.TEMPERATURE else 1
                        corrected = self.pair(position, self.fall_start)[column]
                        self.assertTrue(detector.accept(replace(
                            report, value=corrected, raw_value=str(corrected),
                        )))
            events.extend(detector.advance(self.at(tick), enabled=True))
        return detector, events, observed

    @staticmethod
    def doors(events):
        return [item for item in events if item.kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE)]

    @staticmethod
    def signature(item):
        proof = item.door_close_evidence
        return (item.kind, item.effective_at, item.trace_at, item.detected_at,
                None if proof is None else (proof.candidate_at, proof.confirmed_at))

    def one_close(self, events):
        closes = [item for item in events if item.kind is Kind.DOOR_CLOSE]
        self.assertEqual(len(closes), 1, msg=[self.signature(item) for item in self.doors(events)])
        self.assertIsNotNone(closes[0].door_close_evidence)
        return closes[0]

    def pending_upper(self):
        freeze = self.recovery_start + self.count - 1
        stop = self.recovery_end
        self.assertLess((stop - freeze) * self.step,
                        self.parameters.values["sensor_timeout_seconds"])
        _, events, observed = self.run_inputs(positions=(Position.UPPER,), upper_freeze=freeze, stop=stop)
        self.assertEqual([item.kind for item in self.doors(events)], [Kind.DOOR_OPEN])
        self.assertTrue(any(row.get("original_forms", {}).get("candidate_received_at") is not None
                            and row["at"] >= self.at(self.recovery_start) for row in observed),
                        "Fixture must contain an actual bound but unconfirmed upper close")
        return freeze, stop

    def test_clear_upper_door_sequence_ignores_unused_lower_changes(self):
        _, expected, _ = self.run_inputs(positions=(Position.UPPER,))
        expected = self.doors(expected)
        self.assertEqual([item.kind for item in expected], [Kind.DOOR_OPEN, Kind.DOOR_CLOSE])
        for lower in ("constant", "late_exit", "unavailable", "source_change", "correction"):
            with self.subTest(lower=lower):
                _, actual, _ = self.run_inputs(lower=lower)
                self.assertEqual([self.signature(item) for item in self.doors(actual)],
                                 [self.signature(item) for item in expected])
                self.assertEqual(self.one_close(actual).channels, (Position.UPPER.value,))

    def test_lower_complete_close_owns_its_times_when_upper_is_pending(self):
        freeze, stop = self.pending_upper()
        _, own, _ = self.run_inputs(positions=(Position.LOWER,), lower="complete",
                                   lower_delay=self.count, stop=stop)
        expected = self.one_close(own)
        _, actual, _ = self.run_inputs(lower="complete", lower_delay=self.count,
                                      upper_freeze=freeze, stop=stop)
        close = self.one_close(actual)
        self.assertEqual(self.doors(actual)[0].channels, (Position.UPPER.value,),
                         "Fixture must start with an upper-owned opening")
        self.assertEqual(self.signature(close), self.signature(expected))
        self.assertEqual(close.channels, (Position.LOWER.value,))
        self.assertEqual([item.kind for item in self.doors(actual)], [Kind.DOOR_OPEN, Kind.DOOR_CLOSE])
        self.assertGreater(close.door_close_evidence.confirmed_at, close.door_close_evidence.candidate_at)

    def test_lower_updates_cannot_create_upper_original_freshness(self):
        freeze, stop = self.pending_upper()
        _, actual, _ = self.run_inputs(lower="constant", upper_freeze=freeze, stop=stop)
        self.assertEqual([item.kind for item in self.doors(actual)], [Kind.DOOR_OPEN])
        _, completed, _ = self.run_inputs(positions=(Position.UPPER,), stop=stop)
        self.one_close(completed)  # Own continued originals are the positive control.

    def test_lower_counterform_does_not_veto_a_clear_upper_close(self):
        _, expected, _ = self.run_inputs(positions=(Position.UPPER,))
        _, _, lower_observed = self.run_inputs(
            positions=(Position.LOWER,), lower="counter", lower_delay=self.count,
        )
        close_at = self.one_close(expected).detected_at
        self.assertTrue(any(
            row["at"] == close_at
            and row.get("original_forms", {}).get(
                "temperature_counterform", {},
            ).get("result") is True
            for row in lower_observed
        ), "Fixture must supply an actual lower counterform at the upper decision")
        _, actual, _ = self.run_inputs(lower="counter", lower_delay=self.count)
        self.assertEqual([self.signature(item) for item in self.doors(actual)],
                         [self.signature(item) for item in self.doors(expected)])
        self.assertEqual(self.one_close(actual).channels, (Position.UPPER.value,))

    def test_own_lower_followup_after_carrier_failure_keeps_one_door_episode(self):
        # The lower curve is the same full fixture, later in time. No upper
        # pre-fall values, candidate or timestamp is copied into its input.
        delay = self.climate.door_ticks
        stop = self.end + delay
        _, upper, _ = self.run_inputs(positions=(Position.UPPER,), stop=self.recovery_start)
        opening = self.doors(upper)
        self.assertEqual([item.kind for item in opening], [Kind.DOOR_OPEN])
        _, lower, _ = self.run_inputs(positions=(Position.LOWER,), lower="complete",
                                     lower_delay=delay, stop=stop)
        expected_close = self.one_close(lower)
        _, actual, _ = self.run_inputs(lower="complete", lower_delay=delay,
                                      upper_failure=self.recovery_start, stop=stop)
        doors = self.doors(actual)
        self.assertEqual([item.kind for item in doors], [Kind.DOOR_OPEN, Kind.DOOR_CLOSE])
        self.assertEqual(self.signature(doors[0]), self.signature(opening[0]))
        self.assertEqual(self.signature(doors[1]), self.signature(expected_close))
        self.assertEqual(doors[1].channels, (Position.LOWER.value,))
        self.assertGreaterEqual(doors[1].door_close_evidence.candidate_at,
                                self.at(self.recovery_start + delay))

    def test_bound_lower_counterform_rejects_pending_upper_close_without_rebinding(self):
        freeze, stop = self.pending_upper()
        choices = []
        detector, events, _ = self.run_inputs(
            lower="counter", upper_freeze=freeze, stop=stop,
            selection_records=choices,
        )
        upper, lower = Position.UPPER.value, Position.LOWER.value
        refutations = [
            (index, record) for index, record in enumerate(choices)
            if record["selection"]["leading"] == upper
            and record["selection"]["selected"] == lower
            and record["selection"]["role"] == "clarification"
            and record["selection"]["results"][upper]["status"] == "pending"
            and record["selection"]["results"][lower]["status"] == "refuted"
        ]
        self.assertTrue(
            refutations,
            "Fixture qualification failed: no actual lower refutation of a pending upper close",
        )
        index, rejected = refutations[0]
        selection = rejected["selection"]
        upper_proof = rejected["local_proofs"][upper]
        lower_proof = rejected["local_proofs"][lower]
        self.assertEqual(upper_proof["prefix_at"], lower_proof["prefix_at"])
        self.assertIsNotNone(
            upper_proof["candidate_received_at"],
            "The actual combined input must retain a bound upper close",
        )
        self.assertEqual(
            upper_proof["candidate_received_at"],
            selection["results"][upper]["bound_at"],
        )
        self.assertTrue(
            lower_proof["temperature_fall_reset_reason"] == "renewed_temperature_fall"
            or lower_proof["closing_candidate_reset_reason"] == "temperature_counterform",
            "A generic rejected/missing secondary value is not a physical counterform",
        )
        later = choices[index + 1:]
        self.assertTrue(later, "The refutation needs an observed continuation")
        self.assertTrue(all(
            record["selection"]["results"][upper]["status"] != "unavailable"
            for record in later
        ), "Fixture qualification failed: later upper loss would allow a separate replacement")

        # The same fixed lower curve later supplies a complete own close in
        # isolation. It cannot restore the already rejected upper hypothesis.
        _, own_lower, _ = self.run_inputs(
            positions=(Position.LOWER,), lower="counter", stop=stop,
        )
        self.assertGreater(self.one_close(own_lower).detected_at, rejected["at"])
        self.assertEqual(
            [item.kind for item in self.doors(events)], [Kind.DOOR_OPEN],
        )
        self.assertEqual(self.doors(events)[0].channels, (upper,))
        self.assertEqual(
            detector.latest[Position.UPPER, Quantity.TEMPERATURE].measured_at,
            self.at(freeze),
        )
        self.assertTrue(all(
            record["selection"]["results"][upper]["status"] == "idle"
            for record in later
        ), "Unchanged upper originals must not restore the rejected binding")

    def consumer_boundaries(self, detections):
        self.assertGreaterEqual(
            self.climate.initial[0], self.parameters.values["sauna_min_temperature_c"],
            "The existing climate fixture must admit a direct-presence gang",
        )
        controller = Controller(self.parameters)
        controller.set_temperature(self.climate.initial[0], self.origin)
        controller.set_operation(True, self.origin, session_id="single-leading")
        doors = self.doors(detections)
        self.assertEqual([item.kind for item in doors], [Kind.DOOR_OPEN, Kind.DOOR_CLOSE])
        self.assertLessEqual(
            doors[0].detected_at, doors[1].door_close_evidence.candidate_at,
            "The supplied presence report must be usable before the closing proof",
        )
        for index, item in enumerate(doors):
            controller.set_temperature(self.climate.initial[0], item.detected_at)
            controller.process(Event(
                f"detected-door-{index}", "single-leading", item.kind,
                item.effective_at, item.detected_at, trace_at=item.trace_at,
                door_close_evidence=item.door_close_evidence,
            ), person_evidence_qualified=False)
            if item.kind is Kind.DOOR_OPEN:
                controller.consume_occupancy_input(selected_occupancy(binary_presence(
                    "binary_sensor.presence", "on", item.effective_at, item.detected_at,
                ), item.detected_at))
        active = controller.session.timeline.active
        self.assertIsNotNone(active)
        start = active.started_at
        # Existing controller exit fixture ordering, expressed after this
        # actual detected entry. No physical claim for synthetic door times.
        base = doors[-1].detected_at
        opened = base + timedelta(seconds=self.climate.door_ticks * self.step)
        absent = opened + timedelta(seconds=self.step)
        ventilated = absent + timedelta(seconds=self.step)
        controller.set_temperature(self.climate.initial[0], opened)
        controller.process(Event("exit-open", "single-leading", Kind.DOOR_OPEN, opened, opened),
                           person_evidence_qualified=False)
        controller.consume_occupancy_input(selected_occupancy(binary_presence(
            "binary_sensor.presence", "off", absent, absent,
        ), absent))
        controller.process(Event("exit-vent", "single-leading", Kind.VENTILATION,
                                 ventilated, ventilated), person_evidence_qualified=False)
        self.assertIsNone(controller.session.timeline.active)
        self.assertEqual(len(controller.session.timeline.completed), 1)
        gang = controller.session.timeline.completed[0]
        return (start, gang.ended_at, controller.session.timeline.gang_count,
                controller.session.after_run.requested_at,
                tuple(item.kind for item in controller.session.timeline.processed))

    def test_one_door_booking_preserves_direct_presence_and_exit_boundaries(self):
        _, upper, _ = self.run_inputs(positions=(Position.UPPER,))
        _, dual, _ = self.run_inputs(lower="late_exit")
        self.assertEqual(self.consumer_boundaries(dual), self.consumer_boundaries(upper))


if __name__ == "__main__":
    unittest.main()
