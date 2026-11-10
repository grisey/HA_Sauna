"""B4d Details · Steuerung: Fristen und Regeln (K-R2) und gebuchte Gänge."""

import unittest
from datetime import UTC, datetime, timedelta

from custom_components.ha_sauna.core.control_rules import control_rules, gang_bookings

T0 = datetime(2026, 11, 2, 16, 13, tzinfo=UTC)


def at(seconds):
    return (T0 + timedelta(seconds=seconds)).isoformat()


def gang(gang_id, start, *, infusion=None, kind="presence_entry", end=None, reason=None):
    return {
        "gang_id": gang_id, "started_at": at(start), "detected_at": at(start + 50),
        "recognition_kind": kind,
        "infusion_events": [{"event_id": f"i-{gang_id}", "detected_at": at(infusion)}]
        if infusion is not None else [],
        "ended_at": at(end) if end is not None else None, "end_reason": reason,
    }


PARAMETERS = {
    "thermostat_cooldown_minutes": 5.0, "minimum_heating_minutes": 10.0,
    "after_run_minutes": 5.0, "oven_cooling_max_minutes": 15.0,
    "sauna_min_temperature_c": 60.0, "readiness_hysteresis_c": 1.0, "readiness_offset_c": 2.0,
}


def session(configuration=True):
    first = gang("g1", 3600, infusion=3700, end=4800, reason="ventilation")
    provisional = gang("g2", 6000, end=6100, reason="absence")
    confirmed = gang("g3", 7000, kind="presence_confirmed", end=8000, reason="manual")
    payload = {
        "timeline": {"completed": [first, provisional, confirmed], "active": None,
                     "retracted": [gang("g0", 1000)]},
        "after_run_history": [
            {"phase_id": "g1", "started_at": at(4900), "ends_at": at(5554)},
        ],
        "after_run": {"phase_id": "g3", "started_at": at(8100), "ends_at": None,
                      "pending_start": True},
        "target_history": [{"at": at(0), "target_c": 85}, {"at": at(4900), "target_c": 85},
                           {"at": at(4901), "target_c": 88}, {"at": at(9000), "target_c": 90}],
        "base_phases": [{"at": at(0), "phase": "aufheizen"}],
    }
    if configuration:
        payload["configuration"] = {
            "parameters": PARAMETERS, "control_mode": "automatic", "program_mode": "progressive",
            "selected_program_id": "hw",
            "temperature_programs": [{"id": "hw", "name": "Höhenwanderung", "start_c": 85,
                                      "end_c": 93, "distribution_gangs": 4}],
        }
    return payload


class ControlRuleTests(unittest.TestCase):
    def test_rules_come_from_the_stored_configuration(self):
        rules = control_rules(session())
        self.assertTrue(rules["configured"])
        self.assertEqual(rules["heating_pause_minutes"], 5.0)
        self.assertEqual(rules["minimum_heating_minutes"], 10.0)
        self.assertEqual((rules["oven_cooling_minimum_minutes"],
                          rules["oven_cooling_maximum_minutes"]), (5.0, 15.0))
        self.assertEqual(rules["minimum_temperature_c"], 60.0)
        self.assertEqual(rules["band"], {"below_c": 1.0, "above_c": 2.0})
        self.assertEqual(rules["targets"], [85, 88, 90], "consecutive repeats collapse")
        self.assertEqual(rules["program"], {"kind": "program", "name": "Höhenwanderung"})

    def test_oven_cooling_with_duration_gang_and_pending_phase(self):
        first, pending = control_rules(session())["oven_cooling"]
        self.assertEqual((first["started_at"], first["ended_at"]), (at(4900), at(5554)))
        self.assertAlmostEqual(first["minutes"], 654 / 60)
        self.assertEqual(first["gang"], 1)
        self.assertFalse(first["running"])
        self.assertEqual(pending["gang"], 2, "the presence-confirmed gang is the second confirmed one")
        self.assertTrue(pending["running"])
        self.assertTrue(pending["pending"])
        self.assertIsNone(pending["minutes"])

    def test_without_stored_configuration_no_current_setting_is_used(self):
        rules = control_rules(session(configuration=False))
        self.assertFalse(rules["configured"])
        self.assertIsNone(rules["heating_pause_minutes"])
        self.assertIsNone(rules["band"])
        self.assertEqual(rules["targets"], [85, 88, 90])


class GangBookingTests(unittest.TestCase):
    def test_numbers_confirmation_times_and_retracted_gangs(self):
        bookings = gang_bookings(session()["timeline"])
        self.assertEqual([item["gang_id"] for item in bookings], ["g1", "g2", "g3"])
        first, provisional, confirmed = bookings
        self.assertEqual((first["number"], first["confirmed_at"]), (1, at(3700)))
        self.assertEqual(first["detected_at"], at(3650))
        self.assertEqual((first["ended_at"], first["end_reason"]), (at(4800), "ventilation"))
        self.assertIsNone(provisional["number"])
        self.assertIsNone(provisional["confirmed_at"])
        self.assertFalse(provisional["confirmed"])
        self.assertEqual((confirmed["number"], confirmed["confirmed_at"]), (2, at(7050)))

    def test_active_gang_is_booked_and_marked(self):
        timeline = {"completed": [], "retracted": [],
                    "active": gang("g9", 100, infusion=200)}
        (booking,) = gang_bookings(timeline)
        self.assertTrue(booking["active"])
        self.assertEqual(booking["number"], 1)
        self.assertIsNone(booking["ended_at"])


if __name__ == "__main__":
    unittest.main()
