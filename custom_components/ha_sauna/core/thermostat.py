"""Eigener Thermostat. Reale Ausgabe ab jetzt; keine rückdatierten Befehle."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from math import isfinite

from .contracts import ControlInputs
from .models import ThermostatState
from .parameters import Parameters


@dataclass(frozen=True)
class Decision:
    at: datetime
    heat: bool
    reason: str
    session_id: str | None = None
    created_at: datetime | None = None


def temperature_limits(
    target: float | None, parameters: Parameters
) -> tuple[float | None, float | None]:
    """Return restart and switch-off thresholds, both relative to the setpoint."""
    if target is None:
        return None, None
    return (
        target - parameters.values["readiness_hysteresis_c"],
        target + parameters.values["readiness_offset_c"],
    )


def evaluate(
    state: ThermostatState,
    *,
    now: datetime,
    parameters: Parameters,
    temperature: float | None,
    enabled: bool,
    gang: bool = False,
    after_run: bool = False,
    inputs: ControlInputs | None = None,
    protection: tuple[str, ...] = (),
    inhibits: tuple[str, ...] = (),
    heating_since: datetime | None = None,
    heating_active: bool = False,
    target_temperature: float | None = None,
    pure_hysteresis: bool = False,
):
    """Return the present heat command using the fixed demand priority.

    Positive live gang and temporary-door levels bypass the normal temperature
    cut-off and thermostat cooldown. They never bypass operation, protection,
    inhibitions, or a missing/invalid selected regulation temperature.
    Pure hysteresis ignores session inputs and heating timers.
    """
    values = parameters.values
    controls = inputs if inputs is not None else ControlInputs(
        gang_heat_demand=gang, cooling=after_run
    )

    def result(demand, reason, cooldown=state.cooldown_until):
        if pure_hysteresis:
            cooldown = None
        return replace(state, demand=demand, cooldown_until=cooldown), Decision(
            now, demand, reason
        )

    if protection:
        return result(False, "protection:" + ",".join(protection))
    if not enabled:
        return result(False, "operation_off")
    if inhibits:
        return result(False, "inhibit:" + ",".join(inhibits))
    target = (
        target_temperature
        if target_temperature is not None
        else values.get("target_temperature_c")
    )
    if target is None:
        return result(False, "temperature_configuration_required")
    if temperature is None or not isfinite(temperature):
        return result(False, "upper_temperature_unavailable")
    if not pure_hysteresis and controls.cooling:
        return result(False, "after_run")

    if not pure_hysteresis and controls.gang_heat_demand:
        return result(True, "gang_heat_demand", None)
    if not pure_hysteresis and controls.temporary_door_heat:
        return result(True, "temporary_door_heat", None)

    if (
        not pure_hysteresis
        and (state.demand or heating_active)
        and heating_since is not None
        and now
        < heating_since
        + timedelta(seconds=parameters.seconds("minimum_heating_minutes"))
    ):
        return result(True, "minimum_heating")

    restart_temperature, stop_temperature = temperature_limits(target, parameters)
    if temperature >= stop_temperature:
        cooldown = (
            now + timedelta(seconds=parameters.seconds("thermostat_cooldown_minutes"))
            if state.demand and not pure_hysteresis
            else state.cooldown_until
        )
        return result(False, "temperature_reached", cooldown)
    if not pure_hysteresis and state.cooldown_until and now < state.cooldown_until:
        return result(False, "thermostat_cooldown")
    if temperature <= restart_temperature:
        return result(True, "below_target", None)
    return result(state.demand, "hysteresis_band")
