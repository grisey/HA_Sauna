"""Bound close evidence survives unknown intervals, not actual invalidation.

Reuse the existing manifest-bound closing fixture without moving its values,
changing policy parameters, or selecting a passing event time. The correction
variant targets an actually stored proof original at its identical causal input
prefix; it does not alter the fixture's climate or original timestamps.
Delivery and invalidation variants are sensor-input checks, not human sessions.
"""

from copy import deepcopy
from dataclasses import replace
import unittest

import test_single_sensor_closing as closing_fixture

from custom_components.ha_sauna.core.models import Position, Quantity
from custom_components.ha_sauna.core.timeline import Kind


class SingleSensorClosingGapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Composition only: none of the source TestCase's methods is inherited.
        closing_fixture.SingleSensorClosingTests.setUpClass()

    def setUp(self):
        self.fixture = closing_fixture.SingleSensorClosingTests(methodName="runTest")

    def seed_bound_upper_close(self):
        fixture = self.fixture
        tick = fixture.recovery_start + fixture.count - 1
        detector, events, _ = fixture.run_inputs(stop=tick + 1)
        self.assertEqual(
            [event.kind for event in fixture.doors(events)], [Kind.DOOR_OPEN],
            "The unchanged fixture must bind an upper close before confirming it",
        )
        self.assertEqual(fixture.doors(events)[0].channels, (Position.UPPER.value,))
        episode = detector._position_state(Position.UPPER)["door"]
        self.assertIsNotNone(episode, "The fixture must retain its upper opening")
        candidate = episode.get("original_close")
        self.assertIsNotNone(
            candidate, "The fixed fixture boundary must contain a real bound close",
        )
        self.assertEqual(
            tuple(name for name, _ in candidate["windows"]), (Position.UPPER.value,),
        )
        support = detector._original_time_support_cache[
            Position.UPPER, Quantity.TEMPERATURE
        ][2]
        self.assertIsNotNone(support)
        self.assertTrue(support.pairing_fresh(fixture.at(tick)))
        self.assertEqual(
            support.newest_at,
            detector.latest[Position.UPPER, Quantity.TEMPERATURE].measured_at,
        )
        return detector, events, candidate, support, tick

    @staticmethod
    def upper_candidate(detector):
        return (detector._position_state(Position.UPPER)["door"] or {}).get(
            "original_close",
        )

    @staticmethod
    def upper_originals(detector):
        return {
            quantity: tuple(detector._curve_series[Position.UPPER, quantity]["points"])
            for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY)
        }

    def assert_opening_preserved(self, detector, events, opening_at):
        self.assertTrue(detector.open)
        self.assertEqual(detector._curve_open_effective_at, opening_at)
        self.assertEqual(
            [event.kind for event in self.fixture.doors(events)], [Kind.DOOR_OPEN],
            "Missing originals cannot confirm a close or create another opening",
        )

    def observe_close_choices(self, detector):
        records = []
        original = detector._select_position

        def observe(route, results, owner, trace):
            selected = original(route, results, owner, trace)
            if route == "door_close" and selected is not None:
                records.append({
                    "at": trace["at"],
                    **deepcopy(trace["position_selection"][route]),
                })
            return selected

        detector._select_position = observe
        self.addCleanup(setattr, detector, "_select_position", original)
        return records

    def test_silent_upper_originals_keep_bound_close_without_confirmation(self):
        for delivery in ("ticks_only", "lower_only", "same_stamp_upper_and_lower"):
            with self.subTest(delivery=delivery):
                detector, events, candidate, support, tick = self.seed_bound_upper_close()
                choices = self.observe_close_choices(detector)
                fixture = self.fixture
                last_tick = fixture.recovery_end - 1
                last_at = fixture.at(last_tick)
                upper_samples = tuple(
                    detector.latest[Position.UPPER, quantity]
                    for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY)
                )
                lower_samples = tuple(
                    detector.latest[Position.LOWER, quantity]
                    for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY)
                )
                originals = self.upper_originals(detector)
                context = detector._original_context()
                opening_at = detector._curve_open_effective_at
                bound_at = candidate["received_at"]
                bound_windows = candidate["windows"]
                # Reach the observed original-cadence gap, while the existing
                # sensor timeout still permits every frozen upper measurement.
                self.assertFalse(
                    support.pairing_fresh(last_at),
                    "The existing tail must extend beyond the own original cadence",
                )
                for sample in upper_samples:
                    self.assertLess(
                        (last_at - sample.measured_at).total_seconds(),
                        fixture.parameters.values["sensor_timeout_seconds"],
                        "This case must not enter the separate sensor-timeout path",
                    )
                for current_tick in range(tick + 1, fixture.recovery_end):
                    now = fixture.at(current_tick)
                    if delivery == "same_stamp_upper_and_lower":
                        for sample in upper_samples:
                            self.assertTrue(detector.accept(replace(sample, received_at=now)))
                    if delivery != "ticks_only":
                        for sample in lower_samples:
                            self.assertTrue(detector.accept(replace(
                                sample, received_at=now, measured_at=now,
                            )))
                    events.extend(detector.advance(now, enabled=True))
                    self.assertIs(
                        self.upper_candidate(detector), candidate,
                        f"Bound close lost without a new upper original at {now}; "
                        f"delivery={delivery}, original_support_fresh={support.pairing_fresh(now)}",
                    )
                    self.assertEqual(candidate["received_at"], bound_at)
                    self.assertEqual(candidate["windows"], bound_windows)
                    self.assert_opening_preserved(detector, events, opening_at)
                self.assertEqual(self.upper_originals(detector), originals)
                self.assertEqual(detector._original_context(), context)
                self.assertEqual(
                    [record for record in choices if record["role"] == "replacement"], [],
                    "Waiting without new upper originals must not select a replacement",
                )
                if delivery != "ticks_only":
                    self.assertTrue(
                        any(not support.pairing_fresh(record["at"]) for record in choices),
                        "The lower reports must actually exercise selection after the own cadence gap",
                    )
                for sample in upper_samples:
                    self.assertIsNotNone(detector._value(sample.position, sample.quantity, last_at))

    def test_sensor_timeout_marks_unavailable_without_erasing_historical_close(self):
        detector, events, candidate, support, tick = self.seed_bound_upper_close()
        fixture = self.fixture
        timeout = fixture.parameters.values["sensor_timeout_seconds"]
        last_tick = tick + int(timeout // fixture.step) + 1
        last_at = fixture.at(last_tick)
        samples = tuple(detector.latest[Position.UPPER, quantity]
                        for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY))
        originals = self.upper_originals(detector)
        context = detector._original_context()
        opening_at = detector._curve_open_effective_at
        bound_at, windows = candidate["received_at"], candidate["windows"]
        for sample in samples:
            self.assertGreater((last_at - sample.received_at).total_seconds(), timeout)
        self.assertFalse(support.pairing_fresh(last_at))
        # No fabricated sensor report extends freshness. Current availability
        # must expire while the already observed historical form stays intact.
        for current_tick in range(tick + 1, last_tick + 1):
            events.extend(detector.advance(fixture.at(current_tick), enabled=True))
            self.assertIs(self.upper_candidate(detector), candidate)
            self.assertEqual(candidate["received_at"], bound_at)
            self.assertEqual(candidate["windows"], windows)
            self.assert_opening_preserved(detector, events, opening_at)
        self.assertEqual(self.upper_originals(detector), originals)
        self.assertEqual(detector._original_context(), context)
        self.assertNotIn(Position.UPPER, detector.active_positions)
        self.assertIn("upper_unavailable", detector.faults)
        for sample in samples:
            self.assertIsNone(detector._value(sample.position, sample.quantity, last_at))
        self.assertNotIn(Kind.VENTILATION_COMPLETE, [item.kind for item in events])

    def continue_fixture(self, detector, events, *, through=None,
                         temperature_change=None, humidity_change=None, **advance):
        """Original fixture values; change only delivery/source metadata."""
        fixture = self.fixture
        stop = fixture.end if through is None else through
        for tick in range(detector.index + 1, stop):
            now = fixture.at(tick)
            for position in detector.positions:
                values = fixture.pair(position, tick if position is Position.UPPER else -1)
                for quantity, value in zip(
                    (Quantity.TEMPERATURE, Quantity.HUMIDITY), values, strict=True,
                ):
                    previous = detector.latest[position, quantity]
                    report = replace(previous, received_at=now, measured_at=now,
                                     value=value, raw_value=str(value))
                    if position is Position.UPPER:
                        change = (temperature_change if quantity is Quantity.TEMPERATURE
                                  else humidity_change)
                        if change == "source_replacement":
                            report = replace(report, source=(
                                f"sensor.followup_upper_{quantity.value}_replacement"
                            ))
                        elif change == "received_time":
                            report = replace(report, measured_at=None)
                        elif change == "unavailable":
                            report = replace(report, value=None, raw_value="unavailable")
                    self.assertTrue(detector.accept(report))
            events.extend(detector.advance(now, enabled=True, **advance))

    def test_explicit_temperature_invalidation_discards_the_bound_close(self):
        for cause in ("source_replacement", "received_time", "unavailable"):
            with self.subTest(cause=cause):
                detector, events, candidate, _, tick = self.seed_bound_upper_close()
                series = detector._curve_series[Position.UPPER, Quantity.TEMPERATURE]
                epoch = series["epoch"]
                opening_at = detector._curve_open_effective_at
                boundary = self.fixture.at(tick + 1)
                self.continue_fixture(
                    detector, events, through=tick + 2, temperature_change=cause,
                )
                self.assertGreater(series["epoch"], epoch)
                self.assertIsNot(self.upper_candidate(detector), candidate)
                self.assert_opening_preserved(detector, events, opening_at)
                if cause == "unavailable":
                    self.continue_fixture(detector, events, temperature_change=cause)
                    self.assert_opening_preserved(detector, events, opening_at)
                    continue
                self.assertTrue(all(stamp >= boundary for stamp, _ in series["points"]))
                self.assertEqual(series["time_basis"],
                                 "received" if cause == "received_time" else "measured")
                # This boundary lies inside the existing recovery ramp. Its
                # unchanged remainder has no new decline -> close form after
                # the source reset. Do not invent a successful new close from
                # warming alone, or move the fixture to manufacture one.
                self.assertIsNone(self.upper_candidate(detector))
                local = detector._position_state(Position.UPPER)["door"]
                self.assertIsNotNone(local)
                self.assertNotIn(Position.UPPER, local.get("original_temperature_falls", {}))
                self.assertEqual(tuple(series["points"]), ((
                    boundary, self.fixture.pair(Position.UPPER, tick + 1)[0],
                ),))

    def test_humidity_source_or_loss_does_not_invalidate_own_temperature_close(self):
        for cause in ("source_replacement", "received_time", "unavailable"):
            with self.subTest(cause=cause):
                detector, events, candidate, _, tick = self.seed_bound_upper_close()
                thermal = detector._curve_series[Position.UPPER, Quantity.TEMPERATURE]
                epoch = thermal["epoch"]
                context, bound = candidate["context"], candidate["received_at"]
                self.continue_fixture(
                    detector, events, through=tick + 2, humidity_change=cause,
                )
                self.assertIs(self.upper_candidate(detector), candidate)
                self.assertEqual(candidate["context"], context)
                self.assertEqual(candidate["received_at"], bound)
                self.assertEqual(thermal["epoch"], epoch)
                self.continue_fixture(detector, events, humidity_change=cause)
                closes = [event for event in events if event.kind == Kind.DOOR_CLOSE]
                self.assertEqual(len(closes), 1)
                self.assertEqual(closes[0].channels, (Position.UPPER.value,))
                self.assertIsNotNone(closes[0].door_close_evidence)
                self.assertEqual(closes[0].door_close_evidence.candidate_at, bound)
                self.assertEqual(thermal["epoch"], epoch)

    def test_native_close_callback_and_onset_admission_share_one_confirmation(self):
        results = {}
        for earlier_allowed in (True, False):
            with self.subTest(earlier_allowed=earlier_allowed):
                detector, events, _, _, _ = self.seed_bound_upper_close()
                emitted, checked = [], []
                own_originals = set()

                def admission(kind, onset, now):
                    if kind == Kind.DOOR_CLOSE:
                        checked.append((kind, onset, now))
                        return earlier_allowed
                    return True

                def receive(event):
                    emitted.append(event)
                    if event.kind == Kind.DOOR_CLOSE:
                        own_originals.update(stamp for stamp, _ in
                                             detector._curve_series[
                                                 Position.UPPER, Quantity.TEMPERATURE
                                             ]["points"])

                self.continue_fixture(
                    detector, events, on_detection=receive, onset_allowed=admission,
                )
                closes = [event for event in events if event.kind == Kind.DOOR_CLOSE]
                callbacks = [event for event in emitted if event.kind == Kind.DOOR_CLOSE]
                self.assertEqual(len(closes), 1)
                self.assertEqual(len(callbacks), 1)
                self.assertIs(callbacks[0], closes[0])
                close = closes[0]
                self.assertEqual(close.channels, (Position.UPPER.value,))
                self.assertIsNotNone(close.door_close_evidence)
                self.assertEqual(close.detected_at, close.trace_at)
                self.assertTrue(checked, "The unchanged fixture must exercise actual earlier attribution")
                self.assertEqual(len(checked), 1)
                self.assertEqual(checked[0][2], close.trace_at)
                if earlier_allowed:
                    self.assertEqual(close.effective_at, checked[0][1])
                    self.assertIn(close.effective_at, own_originals)
                    self.assertLess(close.effective_at, close.trace_at)
                else:
                    self.assertEqual(close.effective_at, close.trace_at)
                self.assertFalse(detector.open)
                results[earlier_allowed] = close
        self.assertEqual(results[True].door_close_evidence, results[False].door_close_evidence)
        self.assertEqual(results[True].trace_at, results[False].trace_at)

    def test_first_original_after_silence_keeps_native_anchor_without_tick_confirmation(self):
        detector, events, candidate, support, tick = self.seed_bound_upper_close()
        fixture = self.fixture
        resume_tick = fixture.recovery_end
        resume_at = fixture.at(resume_tick)
        opening_at = detector._curve_open_effective_at
        context = detector._original_context()
        bound_at, bound_windows = candidate["received_at"], candidate["windows"]
        bound_context = candidate["context"]
        sources = {
            quantity: detector.latest[Position.UPPER, quantity].source
            for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY)
        }
        last_upper = detector.latest[Position.UPPER, Quantity.TEMPERATURE].measured_at
        self.assertFalse(support.pairing_fresh(resume_at))
        self.assertLess(
            (resume_at - last_upper).total_seconds(),
            fixture.parameters.values["sensor_timeout_seconds"],
        )
        for current_tick in range(tick + 1, resume_tick + fixture.count):
            now = fixture.at(current_tick)
            if current_tick == resume_tick:
                self.assertIs(
                    self.upper_candidate(detector), candidate,
                    "The gap must preserve the bound proof before original delivery resumes",
                )
                values = fixture.pair(Position.UPPER, current_tick)
                for quantity, value in zip(
                    (Quantity.TEMPERATURE, Quantity.HUMIDITY), values, strict=True,
                ):
                    original = detector.latest[Position.UPPER, quantity]
                    self.assertTrue(detector.accept(replace(
                        original, received_at=now, measured_at=now,
                        value=value, raw_value=str(value),
                    )))
            for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY):
                original = detector.latest[Position.LOWER, quantity]
                self.assertTrue(detector.accept(replace(
                    original, received_at=now, measured_at=now,
                )))
            events.extend(detector.advance(now, enabled=True))
            self.assert_opening_preserved(detector, events, opening_at)
            if current_tick >= resume_tick:
                self.assertIs(
                    self.upper_candidate(detector), candidate,
                    "A temporal gap alone cannot refute the valid bound close",
                )
                self.assertEqual(candidate["received_at"], bound_at)
                self.assertEqual(candidate["windows"], bound_windows)
                self.assertEqual(candidate["context"], bound_context)
        originals = self.upper_originals(detector)[Quantity.TEMPERATURE]
        self.assertEqual(detector._original_context(), context)
        self.assertEqual({
            quantity: detector.latest[Position.UPPER, quantity].source
            for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY)
        }, sources)
        self.assertEqual([stamp for stamp, _ in originals if stamp > last_upper], [resume_at])
        resumed_support = detector._original_time_support_cache[
            Position.UPPER, Quantity.TEMPERATURE
        ][2]
        self.assertIsNotNone(resumed_support)
        self.assertTrue(resumed_support.covers(last_upper, resume_at))
        self.assertFalse(resumed_support.pairing_covers(last_upper, resume_at))

    def seed_correctable_bound_upper_close(self):
        """Target an actually bound original while it is still the latest input.

        This is state-targeted fault injection, not event-time assessment: the
        unchanged full seed identifies the stored proof's last original. A
        second identical input prefix ends exactly there, before later originals
        would make that old report rejectable. No climate value or timestamp is
        replaced, and no cutoff is searched for a passing close decision.
        """
        _, _, reference, _, _ = self.seed_bound_upper_close()
        windows = [window for name, window in reference["windows"]
                   if name == Position.UPPER.value]
        self.assertEqual(len(windows), 1)
        used_at = windows[0].newest_at
        seconds = (used_at - self.fixture.origin).total_seconds()
        tick = int(seconds // self.fixture.step)
        self.assertEqual(self.fixture.at(tick), used_at)
        detector, events, _ = self.fixture.run_inputs(stop=tick + 1)
        candidate = self.upper_candidate(detector)
        self.assertIsNotNone(candidate,
                             "The same causal prefix must already own this bound proof")
        self.assertEqual(candidate["received_at"], reference["received_at"])
        self.assertEqual(candidate["windows"], reference["windows"])
        original = detector.latest[Position.UPPER, Quantity.TEMPERATURE]
        self.assertEqual(original.measured_at, used_at)
        self.assertEqual(tuple(detector._curve_series[
            Position.UPPER, Quantity.TEMPERATURE
        ]["points"])[-1], (used_at, original.value))
        self.assert_opening_preserved(detector, events, detector._curve_open_effective_at)
        return detector, events, candidate, tick

    def test_same_stamp_correction_of_used_temperature_invalidates_bound_close(self):
        for expired in (False, True):
            with self.subTest(after_current_sensor_timeout=expired):
                detector, events, candidate, tick = self.seed_correctable_bound_upper_close()
                delay = (int(self.fixture.parameters.values["sensor_timeout_seconds"]
                             // self.fixture.step) + 1 if expired else 1)
                now = self.fixture.at(tick + delay)
                for waiting_tick in range(tick + 1, tick + delay):
                    events.extend(detector.advance(self.fixture.at(waiting_tick), enabled=True))
                    self.assertIs(self.upper_candidate(detector), candidate)
                original = detector.latest[Position.UPPER, Quantity.TEMPERATURE]
                used_windows = candidate["windows"] + candidate["recovery_windows"]
                self.assertTrue(any(
                    name == Position.UPPER.value
                    and any(stamp == original.measured_at for stamp, _ in window.previous + window.current)
                    for name, window in used_windows
                ), "The corrected sample must belong to the bound or continued thermal proof")
                originals = self.upper_originals(detector)
                context = detector._original_context()
                opening_at = detector._curve_open_effective_at
                # Use the preceding exchange endpoint, not an invented noise multiple.
                value = self.fixture.pair(Position.UPPER, self.fixture.recovery_start - 1)[0]
                self.assertNotEqual(value, original.value)
                self.assertTrue(detector.accept(replace(
                    original, received_at=now, value=value, raw_value=str(value),
                )))
                events.extend(detector.advance(now, enabled=True))
                after = self.upper_originals(detector)
                self.assertEqual(detector._original_context(), context)
                self.assertEqual(after[Quantity.HUMIDITY], originals[Quantity.HUMIDITY])
                self.assertEqual(after[Quantity.TEMPERATURE][:-1], originals[Quantity.TEMPERATURE][:-1])
                self.assertEqual(after[Quantity.TEMPERATURE][-1], (original.measured_at, value))
                self.assertIsNot(self.upper_candidate(detector), candidate)
                self.assert_opening_preserved(detector, events, opening_at)
                if expired:
                    self.assertIsNone(detector._value(Position.UPPER, Quantity.TEMPERATURE, now))
                    self.assertIn("upper_unavailable", detector.faults)
