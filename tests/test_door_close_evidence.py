"""Door-close evidence transport over the existing native-original fixtures.

These checks cover provenance and reset rules, not physical recognition quality.
The observer delegates every decision to the unchanged productive detector.
"""

from dataclasses import replace
import unittest

import test_original_curve_evidence as originals

from custom_components.ha_sauna.core.models import Position, Quantity
from custom_components.ha_sauna.core.timeline import DoorCloseEvidence, Kind


class DoorCloseEvidenceTests(unittest.TestCase):
    def setUp(self):
        # Composition avoids discovering the fixture class's full test suite again.
        self.fixture = originals.OriginalCurveFollowupTests()
        self.fixture.setUp()

    def _observe_closures(self, detector):
        observations = []
        original = detector._original_door_evidence

        def observe(prefixes, channels, now, decision_at, trace, emit):
            result = original(prefixes, channels, now, decision_at, trace, emit)
            if result[2]:
                episode = detector.door_episode
                candidate = episode.get("original_close")
                falls = episode.get("original_temperature_falls", {})
                forms = trace["original_forms"]
                observations.append({
                    "evidence": episode.get("closing_evidence"),
                    "candidate": candidate,
                    "candidate_at": (
                        candidate["received_at"] if candidate is not None else None
                    ),
                    "recoveries": {
                        position: falls.get(position, {}).get("recovery_received_at")
                        for position in result[3]
                    },
                    "level": forms["new_temperature_level"]["result"],
                    "confirmed_at": forms["confirmation_received_at"],
                    "original": forms["new_original"],
                })
            return result

        detector._original_door_evidence = observe
        self.addCleanup(setattr, detector, "_original_door_evidence", original)
        return observations

    def _assert_transport(self, detector, events, observations):
        closures = [item for item in events if item.kind is Kind.DOOR_CLOSE]
        self.assertTrue(closures)
        self.assertEqual(len(closures), len(observations))
        for closure, observed in zip(closures, observations, strict=True):
            evidence = closure.door_close_evidence
            self.assertIsInstance(evidence, DoorCloseEvidence)
            self.assertIs(evidence, observed["evidence"])
            self.assertEqual(evidence.confirmed_at, observed["confirmed_at"])
            self.assertTrue(observed["original"])
            self.assertLess(evidence.candidate_at, evidence.confirmed_at)
            self.assertLessEqual(evidence.confirmed_at, closure.detected_at)
        self.assertTrue(all(
            item.door_close_evidence is None
            for item in events if item.kind is not Kind.DOOR_CLOSE
        ))
        self.assertFalse(detector.open)
        self.assertIsNone(detector.door_episode)
        return closures

    def test_original_close_keeps_its_bound_candidate_through_confirmation(self):
        fixture = self.fixture
        detector, events, candidate = fixture._bind("closing")
        candidate_at = candidate["received_at"]
        self.assertNotIn("closing_evidence", detector.door_episode)
        observations = self._observe_closures(detector)

        events += fixture._ramp(detector, 4 * fixture.count, fixture.recovery_step)

        closures = self._assert_transport(detector, events, observations)
        self.assertEqual(len(closures), 1)
        self.assertIs(observations[0]["candidate"], candidate)
        self.assertEqual(closures[0].door_close_evidence.candidate_at, candidate_at)
        self.assertEqual(
            closures[0].door_close_evidence.confirmed_at,
            candidate["confirmation_received_at"],
        )

    def test_staggered_level_close_uses_both_original_recovery_times(self):
        fixture = self.fixture
        detector, events, _ = fixture._bind("opening")
        observations = self._observe_closures(detector)
        events += fixture._ramp(detector, 4 * fixture.count, fixture.fall_step)
        lower_fall = tuple(
            change if role == "lower_temperature" else 0
            for role, change in zip(originals.CLIMATE, fixture.fall_step, strict=True)
        )
        events += fixture._ramp(detector, 3 * fixture.count, lower_fall)
        self.assertTrue(detector.open)
        self.assertFalse(observations)
        upper_recovery = detector.door_episode["original_temperature_falls"][
            Position.UPPER
        ]["recovery_received_at"]

        # Same staggered-plateau input and endpoint perturbation as the native
        # followup fixture; no shared instantaneous braking decision is supplied.
        plateau = fixture._latest(detector)
        events += fixture._feed(
            detector, (plateau for _ in range(fixture.count - 1)),
        )
        noise = fixture.values["curve_original_noise_temperature_lower"]
        perturbed = tuple(
            value - 2.6 * noise if role == "lower_temperature" else value
            for role, value in zip(originals.CLIMATE, plateau, strict=True)
        )
        events += fixture._feed(detector, (perturbed,))
        self.assertFalse(observations)
        events += fixture._feed(
            detector, (plateau for _ in range(fixture.tail_ticks)),
        )

        closures = self._assert_transport(detector, events, observations)
        self.assertEqual(len(closures), 1)
        observed = observations[0]
        self.assertIsNone(observed["candidate"])
        self.assertTrue(observed["level"])
        recoveries = observed["recoveries"]
        self.assertEqual(set(recoveries), set(detector.positions))
        self.assertTrue(all(stamp is not None for stamp in recoveries.values()))
        self.assertEqual(recoveries[Position.UPPER], upper_recovery)
        self.assertLess(upper_recovery, recoveries[Position.LOWER])
        self.assertEqual(
            closures[0].door_close_evidence.candidate_at,
            recoveries[Position.LOWER],
        )

        # The existing fixture's next door cycle must acquire its own proof.
        previous = closures[0].door_close_evidence
        events += fixture._ramp(detector, 4 * fixture.count, fixture.fall_step)
        events += fixture._ramp(
            detector, fixture.tail_ticks, (0,) * len(originals.CLIMATE),
        )
        closures = self._assert_transport(detector, events, observations)
        self.assertEqual(len(closures), 2)
        self.assertIs(closures[0].door_close_evidence, previous)
        self.assertGreater(
            closures[1].door_close_evidence.candidate_at, previous.confirmed_at,
        )

    def test_temperature_loss_discards_pending_beginning_without_fabricating_one(self):
        fixture = self.fixture
        detector, events, candidate = fixture._bind("closing")
        observations = self._observe_closures(detector)
        invalid_at = fixture._at(detector.index + 1)
        for position in detector.positions:
            original = detector.latest[position, Quantity.TEMPERATURE]
            detector.accept(replace(
                original, value=None, raw_value="unavailable",
                received_at=invalid_at, measured_at=invalid_at,
            ))
        events += detector.advance(invalid_at, enabled=True)
        self.assertTrue(detector.open)
        self.assertNotIn("original_close", detector.door_episode)
        self.assertFalse(detector.door_episode.get("original_temperature_falls"))
        self.assertNotIn("closing_evidence", detector.door_episode)

        # Ticks provide no new originals and must not manufacture a replacement
        # candidate timestamp from either the old candidate or the current clock.
        events += detector.advance(
            fixture._at(detector.index + fixture.count), enabled=True,
        )
        self.assertFalse(observations)
        self.assertNotIn(Kind.DOOR_CLOSE, [item.kind for item in events])
        self.assertTrue(all(item.door_close_evidence is None for item in events))
        self.assertIsNot(detector.door_episode.get("original_close"), candidate)

    def test_renewed_fall_replaces_the_old_close_beginning(self):
        fixture = self.fixture
        detector, events, previous = fixture._bind("closing")
        previous_at = previous["received_at"]
        observations = self._observe_closures(detector)
        events += fixture._ramp(
            detector, fixture.count - 1, fixture.recovery_step,
        )
        events += fixture._ramp(detector, 3 * fixture.count, fixture.fall_step)
        self.assertTrue(detector.open)
        self.assertFalse(observations)
        self.assertIsNot(detector.door_episode.get("original_close"), previous)
        self.assertNotIn("closing_evidence", detector.door_episode)

        events += fixture._ramp(
            detector, fixture.count - 1, fixture.recovery_step,
        )
        self.assertFalse(observations)
        events += fixture._ramp(detector, 4 * fixture.count, fixture.recovery_step)
        closures = self._assert_transport(detector, events, observations)
        self.assertEqual(len(closures), 1)
        self.assertIsNot(observations[0]["candidate"], previous)
        self.assertGreater(closures[0].door_close_evidence.candidate_at, previous_at)


if __name__ == "__main__":
    unittest.main()
