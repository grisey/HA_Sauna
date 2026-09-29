"""Präsenzbelege bleiben unabhängig von Gangzählung und Steuerung."""
import unittest
from datetime import UTC, datetime, timedelta

from custom_components.ha_sauna.core.presence import (
    PresenceProjection, ProxyPresenceSource, binary_presence,
)
from custom_components.ha_sauna.core.timeline import Event, Kind

T = datetime(2026, 9, 26, tzinfo=UTC)


class PresenceTests(unittest.TestCase):
    def test_proxy_preserves_evidence_times_and_retraction_is_not_absence(self):
        event = Event("person", "s", Kind.PERSON_WEAK, T, T + timedelta(minutes=3))
        present = ProxyPresenceSource.present(event)
        self.assertEqual((present.source_ref, present.effective_at, present.received_at),
                         (event.event_id, event.effective_at, event.detected_at))
        self.assertEqual(present.assertion, "provisional_proxy")
        end = Event("expire", "s", Kind.CONFIRMATION_EXPIRED, T, T)
        retract = ProxyPresenceSource.retract(end, event.event_id)
        self.assertEqual((retract.occupancy, retract.assertion), ("unknown", "proxy_retraction"))
        with self.assertRaises(ValueError):
            ProxyPresenceSource.present(Event("infusion", "s", Kind.INFUSION, T, T))

    def test_binary_mapping_and_no_age_expiry(self):
        for state, occupancy, available in [
            ("on", "present", True), ("off", "absent", True),
            ("unknown", "unknown", False), ("unavailable", "unknown", False),
            (None, "unknown", False),
        ]:
            report = binary_presence("binary_sensor.any", state, T, T + timedelta(days=100))
            self.assertEqual((report.occupancy, report.available), (occupancy, available))
            self.assertEqual(report.effective_at, T)

    def test_observations_survive_without_infusion_and_never_lead_proxy(self):
        projection = PresenceProjection()
        event = Event("person", "s", Kind.PERSON_STRONG, T, T)
        proxy = ProxyPresenceSource.present(event)
        projection.accept(proxy)
        for state, minute in [("on", 1), ("on", 2), ("unknown", 3), ("on", 4), ("off", 5)]:
            at = T + timedelta(minutes=minute)
            projection.accept(binary_presence("binary_sensor.any", state, at, at))
        self.assertEqual(projection.current, proxy)
        observations = projection.observations
        self.assertEqual(len(observations), 2)
        self.assertEqual(observations[0].end_reason, "unknown")
        self.assertEqual(observations[1].end_reason, "absent")
        self.assertEqual(observations[0].started_at, T + timedelta(minutes=1))
        with self.assertRaises(ValueError):
            PresenceProjection("binary_sensor.any")

    def test_reload_duplicate_retains_original_receipt(self):
        projection = PresenceProjection()
        first = binary_presence("binary_sensor.any", "on", T, T)
        reload = binary_presence("binary_sensor.any", "on", T, T + timedelta(days=1))
        self.assertTrue(projection.accept(first))
        self.assertFalse(projection.accept(reload))
        self.assertEqual(len(projection.observations), 1)
        self.assertEqual(projection.external[first.source].received_at, T)

    def test_out_of_order_archive_restores_observation(self):
        projection = PresenceProjection()
        projection.accept(binary_presence("binary_sensor.any", "off", T + timedelta(hours=1), T + timedelta(hours=1)))
        projection.accept(binary_presence("binary_sensor.any", "on", T, T))
        self.assertEqual(projection.external["binary_sensor.any"].occupancy, "absent")
        self.assertEqual(projection.observations[0].ended_at, T + timedelta(hours=1))
