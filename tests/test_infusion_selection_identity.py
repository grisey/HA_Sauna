"""Publication identity checks using the existing native two-pulse fixture.

The input-path regression uses unmodified detector mathematics. The smaller
projection checks vary ownership/continuity records obtained from that fixture;
they test the selection boundary, not physical event quality.
"""

from copy import deepcopy
import unittest

import test_detector as fixtures

from custom_components.ha_sauna.core.detector import Detector
from custom_components.ha_sauna.core.models import Position, Quantity
from custom_components.ha_sauna.core.timeline import Kind


class InfusionSelectionIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = fixtures.InfusionLifecycleTests(methodName="runTest")
        fixture.setUp()
        cls.fixture = fixture
        cls.points, _, cls.next_rise, cls.next_fall = fixture._scenario(merged=False)
        detector = Detector(fixture.parameters, fixtures.T0)
        events = []
        cls.retained = None
        for second in range(0, cls.next_fall + fixture.step, fixture.step):
            cls.send(detector, second, upper_second_pulse=False)
            events.extend(detector.advance(fixture._at(second), enabled=True))
            state = detector._position_state(Position.LOWER)
            episode, current = state["infusion"], state["infusion_candidate"]
            if episode is None or current is None:
                continue
            first = episode["original_start_component"] or episode["original_component"]
            if (episode.get("confirmation_ready") and episode["confirmed_detection"] is None
                    and first is not None and current is not first
                    and not current["qualified"] and second >= cls.next_rise):
                cls.retained = detector, state, first, current, second
                break
        if cls.retained is None:
            raise AssertionError("Existing two-pulse fixture did not reach the retained-ready/new-pending boundary")
        if any(event.kind is Kind.INFUSION for event in events):
            raise AssertionError("An idle available primary must not publish the unused secondary pulse")

    @classmethod
    def send(cls, detector, second, *, upper_second_pulse):
        fixture = cls.fixture
        for position in detector.positions:
            humidity = (fixture._value(cls.points, second)
                        if position is Position.LOWER or upper_second_pulse and second >= cls.next_rise
                        else fixture.level)
            for quantity, value in ((Quantity.TEMPERATURE, fixture.temperature),
                                    (Quantity.HUMIDITY, humidity)):
                accepted = detector.accept(fixtures.measurement(
                    position, quantity, value, second,
                    measured=fixture._at(second),
                    source=f"sensor.identity_{position.value}_{quantity.value}",
                ))
                if not accepted:
                    raise AssertionError("Existing fixture original was not accepted")

    def test_new_pending_pulse_cannot_publish_the_old_secondary_episode(self):
        fixture = self.fixture
        detector = Detector(fixture.parameters, fixtures.T0)
        events = []
        old = None
        observed_distinct_binding = False
        for second in range(0, self.next_fall + fixture.step, fixture.step):
            self.send(detector, second, upper_second_pulse=True)
            events.extend(detector.advance(fixture._at(second), enabled=True))
            state = detector._position_state(Position.LOWER)
            episode, candidate = state["infusion"], state["infusion_candidate"]
            if (second < self.next_rise and episode is not None
                    and episode.get("confirmation_ready")):
                old = episode
            if (old is not None and episode is old and candidate is not None
                    and candidate is not old["original_start_component"]
                    and not candidate["qualified"]):
                observed_distinct_binding = True
                self.assertIsNone(old["confirmed_detection"])
                self.assertTrue(old["confirmation_ready"])
            for event in events:
                if event.kind is Kind.INFUSION:
                    self.assertGreaterEqual(event.effective_at, fixture._at(self.next_rise))
        self.assertIsNotNone(old)
        self.assertTrue(observed_distinct_binding)
        self.assertIsNone(old["confirmed_detection"])
        self.assertTrue(old["confirmation_ready"])
        infusions = [event for event in events if event.kind is Kind.INFUSION]
        self.assertEqual(len(infusions), 1)

    def test_ready_projection_keeps_its_own_binding_and_connection_state(self):
        detector, state, first, current, _ = self.retained
        self.assertNotEqual(first["received_at"], current["received_at"])
        expected_span = detector._bound_position_span(first)
        self.assertNotEqual(expected_span, detector._bound_position_span(current))
        for original_unknown in (False, True):
            for candidate_unknown in (False, True):
                with self.subTest(original=original_unknown, candidate=candidate_unknown):
                    own = {**first, "connection_unknown": original_unknown}
                    new = {**current, "connection_unknown": candidate_unknown}
                    episode = {**state["infusion"], "original_start_component": own}
                    local = {**state, "infusion": episode, "infusion_candidate": new}
                    result = detector._infusion_position_result(
                        Position.LOWER, local, new, {"terminal_negative": True}, available=True,
                    )
                    self.assertEqual(result["status"], "idle" if original_unknown else "confirmed")
                    self.assertEqual(result["bound_at"], first["received_at"])
                    self.assertEqual(result["span"], expected_span)
                    self.assertIs(episode["original_start_component"], own)
                    self.assertIs(local["infusion_candidate"], new)
                    self.assertEqual(own["qualified"], first["qualified"])
                    self.assertIsNone(episode["confirmed_detection"])

    def test_matching_secondary_can_still_confirm_a_pending_primary(self):
        fixture = self.fixture
        detector = Detector(fixture.parameters, fixtures.T0)
        infusions = []
        for second in range(0, self.next_rise, fixture.step):
            for position in detector.positions:
                own_second = max(0, second - fixture.step) if position is Position.UPPER else second
                humidity = fixture._value(self.points, own_second)
                for quantity, value in ((Quantity.TEMPERATURE, fixture.temperature),
                                        (Quantity.HUMIDITY, humidity)):
                    self.assertTrue(detector.accept(fixtures.measurement(
                        position, quantity, value, second, measured=fixture._at(second),
                    )))
            infusions.extend(event for event in detector.advance(
                fixture._at(second), enabled=True,
            ) if event.kind is Kind.INFUSION)
            if infusions:
                break
        self.assertEqual(len(infusions), 1)
        self.assertEqual(infusions[0].channels, (Position.LOWER.value,))
        episode = detector._position_state(Position.LOWER)["infusion"]
        self.assertIs(episode["confirmed_detection"], infusions[0])
        self.assertEqual(episode["onset_at"], infusions[0].effective_at)

    def test_retained_proof_is_not_relabelled_after_a_native_source_change(self):
        detector, state, first, _, second = deepcopy(self.retained)
        retained_state = dict(state)
        original = dict(first["position_bindings"][Position.LOWER])
        following = second + self.fixture.step
        self.assertTrue(detector.accept(fixtures.measurement(
            Position.LOWER, Quantity.HUMIDITY,
            self.fixture._value(self.points, following), following,
            measured=self.fixture._at(following), source="sensor.identity_replacement",
        )))
        detector.advance(self.fixture._at(following), enabled=True)
        result = detector._infusion_position_result(
            Position.LOWER, retained_state, retained_state["infusion_candidate"], {}, available=True,
        )
        # Epoch invalidation may clear the live local state. The retained
        # evidence object itself must still describe its original source.
        self.assertEqual(first["position_bindings"][Position.LOWER], original)
        for key, value in original.items():
            self.assertEqual(result[key], value)
        self.assertNotEqual(original, detector._position_source_context(Position.LOWER))
        self.assertNotEqual(result["status"], "confirmed")


if __name__ == "__main__":
    unittest.main()
