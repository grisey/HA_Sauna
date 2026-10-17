"""A fresh renewed cooling block must veto an older door-close binding."""

import unittest

import test_door_candidate_regressions as door_fixtures
from test_original_curve_evidence import CLIMATE

from custom_components.ha_sauna.core.timeline import Kind


class DoorRenewedFallTests(unittest.TestCase):
    def setUp(self):
        self.door_fixture = door_fixtures.DoorCandidateRegressionTests()
        self.door_fixture.setUp()
        self.fixture = self.door_fixture.fixture

    def test_moderate_renewed_fall_vetoes_the_older_plateau_close(self):
        fixture = self.fixture
        detector, events = self.door_fixture._open_door()
        # Reuse the established original fall, then one K-section at its
        # current level. This is shorter than the close confirmation support.
        events += fixture._ramp(detector, fixture.count, (0, 0, 0, 0))
        self.assertNotIn(Kind.DOOR_CLOSE, [event.kind for event in events])
        self.assertTrue(detector.open)
        # Both temperature roles now fall at half the original fixture slope.
        # Relative to the old steep fall this still looks like braking, but
        # relative to the intervening plateau it is a new sustained descent.
        renewed_delta = tuple(
            change / 2 if role.endswith("temperature") else 0
            for role, change in zip(CLIMATE, fixture.fall_step, strict=True)
        )
        events += fixture._ramp(detector, fixture.tail_ticks, renewed_delta)
        self.assertNotIn(Kind.DOOR_CLOSE, [event.kind for event in events])
        self.assertTrue(detector.open)


if __name__ == "__main__":
    unittest.main()
