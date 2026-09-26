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
):
    values = parameters.values
    controls = inputs if inputs is not None else ControlInputs(
        gang_veto=gang, cooling=after_run
    )

    def result(demand, reason, cooldown=state.cooldown_until):
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
    if controls.cooling:
        return result(False, "after_run")
    if temperature is None or not isfinite(temperature):
        return result(False, "upper_temperature_unavailable")
    if (
        (state.demand or heating_active)
        and heating_since is not None
        and now
        < heating_since
        + timedelta(seconds=parameters.seconds("minimum_heating_minutes"))
    ):
        # A manual command may hand a real, confirmed heat run back to the
        # regulator without changing its previous demand bit.  Minimum runtime
        # begins at that feedback, never at the command or gang signal.
        return result(True, "minimum_heating")
    readiness_target = target + values["readiness_offset_c"]
    if controls.gang_veto and (state.demand or heating_active):
        # A gang only vetoes an otherwise regular switch-off of heat that is
        # already demanded or actually running.  It does not create an OFF→ON
        # start, and protection, operation, cooling and invalid-temperature
        # rules above keep their priority.
        return result(True, "gang_veto")
    if temperature >= readiness_target:
        if controls.door_request and not state.demand:
            # Actual feedback starts minimum heating; a pulse cannot fabricate
            # that history or introduce a new holding period at the limit.
            return result(False, "door_request_at_limit")
        cooldown = (
            now + timedelta(seconds=parameters.seconds("thermostat_cooldown_minutes"))
            if state.demand
            else state.cooldown_until
        )
        return result(False, "temperature_reached", cooldown)
    if controls.door_request and not state.demand:
        return result(True, "door_request", None)
    if state.cooldown_until and now < state.cooldown_until:
        return result(False, "thermostat_cooldown")
    if temperature <= readiness_target - values["readiness_hysteresis_c"]:
        return result(True, "below_target", None)
    return result(state.demand, "hysteresis_band")
