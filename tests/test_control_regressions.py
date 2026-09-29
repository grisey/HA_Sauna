"""Regression coverage for confirmed manual heat and door-cycle handoff."""
import unittest
from datetime import UTC, datetime, timedelta

from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.timeline import Event, Kind


T0 = datetime(2026, 9, 22, 17, tzinfo=UTC)


def at(seconds: int) -> datetime:
    return T0 + timedelta(seconds=seconds)


def event(controller: Controller, kind: Kind, seconds: int):
    return controller.process(
        Event(
            f"{kind.value}-{seconds}",
            controller.session.session_id,
            kind,
            at(seconds),
            at(seconds),
        )
    )


def controller() -> Controller:
    value = Controller(
        Parameters(
            {
                "target_temperature_c": 80,
                "readiness_offset_c": 3,
                "readiness_hysteresis_c": 3,
                "minimum_heating_minutes": 10,
                "thermostat_cooldown_minutes": 5,
            }
        )
    )
    value.set_temperature(80, at(0))
    value.report_contactor(False, at(0))
    value.report_heating(False, at(0))
    value.set_operation(True, at(0), session_id="session")
    value.report_contactor(True, at(1))
    value.report_heating(True, at(1))
    return value


def stop_then_manually_restart(value: Controller) -> None:
    value.set_temperature(83, at(700))
    value.report_contactor(False, at(701))
    value.report_heating(False, at(701))
    value.set_temperature(81, at(720))
    event(value, Kind.DOOR_OPEN, 730)
    value.set_heater_override(True, at(731))
    value.report_contactor(True, at(732))
    value.report_heating(True, at(732))
    event(value, Kind.DOOR_CLOSE, 750)


class ControlRegressionTests(unittest.TestCase):
    def test_power_expiry_splits_heating_at_ttl_even_on_exact_tick(self):
        for first_tick in (5, 6):
            with self.subTest(first_tick=first_tick):
                value = controller()
                value.report_fallback_heating(False, at(2))
                value.report_power(1500, at(5), at(2))
                value.advance(at(first_tick))
                value.advance(at(8))
                self.assertEqual(value.session.heating.intervals[0].ended_at, at(5))
                self.assertEqual(value.session.heating.elapsed_seconds, 4)
                self.assertEqual(value.session.energy.measured_seconds, 3)

    def test_confirmed_manual_heat_survives_gang_start_during_old_cooldown(self):
        value = controller()
        stop_then_manually_restart(value)

        event(value, Kind.PERSON_STRONG, 760)

        assert value.last_decision.heat is True
        assert value.last_decision.reason == "gang_heat_demand"
        assert value.heater_override is None

    def test_live_gang_demand_keeps_confirmed_heat_after_minimum_runtime(self):
        value = controller()
        stop_then_manually_restart(value)
        event(value, Kind.PERSON_STRONG, 760)

        value.advance(at(1400))
        assert value.last_decision.heat is True
        assert value.last_decision.reason == "gang_heat_demand"

        idle = controller()
        idle.set_temperature(83, at(700))
        idle.report_contactor(False, at(701))
        idle.report_heating(False, at(701))
        idle.set_temperature(81, at(720))
        assert idle.last_decision.heat is False

    def test_open_while_heating_leaves_one_close_request_if_heat_stops_before_close(self):
        value = controller()
        event(value, Kind.DOOR_OPEN, 700)
        assert value.door_request.eligible_open is True

        value.set_temperature(83, at(720))
        value.report_contactor(False, at(721))
        value.report_heating(False, at(721))
        value.set_temperature(81, at(740))
        event(value, Kind.DOOR_CLOSE, 750)

        assert value.last_decision.heat is True
        assert value.last_decision.reason == "temporary_door_heat"
        assert value.door_request.eligible_open is False
        assert value.door_request.door_open is False
        assert event(value, Kind.DOOR_CLOSE, 750).changed is False
