"""Eigener Thermostat. Reale Ausgabe ab jetzt; keine rückdatierten Befehle."""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from math import isfinite

from .models import ThermostatState
from .parameters import Parameters


@dataclass(frozen=True)
class Decision:
    at: datetime
    heat: bool
    reason: str


def evaluate(state: ThermostatState, *, now: datetime, parameters: Parameters,
             temperature: float | None, enabled: bool, gang: bool,
             cooling: bool, after_run: bool, protection: tuple[str, ...] = (),
             inhibits: tuple[str, ...] = (),
             heating_since: datetime | None = None, target_temperature: float | None = None):
    values = parameters.values
    def result(demand, reason, cooldown=state.cooldown_until):
        return replace(state, demand=demand, cooldown_until=cooldown), Decision(now, demand, reason)

    if protection:
        return result(False, "protection:" + ",".join(protection))
    if not enabled:
        return result(False, "operation_off")
    if inhibits:
        return result(False, "inhibit:" + ",".join(inhibits))
    target = target_temperature if target_temperature is not None else values.get("target_temperature_c")
    limit = values.get("safety_temperature_c")
    if target is None or limit is None:
        return result(False, "temperature_configuration_required")
    if cooling:
        return result(False, "forced_cooling")
    if after_run:
        return result(False, "after_run")
    if temperature is None or not isfinite(temperature):
        return result(False, "upper_temperature_unavailable")
    if gang:
        return result(True, "gang")
    if (state.demand and heating_since is not None
            and now < heating_since + timedelta(seconds=parameters.seconds("minimum_heating_minutes"))):
        return result(True, "minimum_heating")
    readiness_target = target + values["readiness_offset_c"]
    if temperature >= readiness_target:
        cooldown = now + timedelta(seconds=parameters.seconds("thermostat_cooldown_minutes")) if state.demand else state.cooldown_until
        return result(False, "temperature_reached", cooldown)
    if state.cooldown_until and now < state.cooldown_until:
        return result(False, "thermostat_cooldown")
    if temperature <= readiness_target - values["readiness_hysteresis_c"]:
        return result(True, "below_target", None)
    return result(state.demand, "hysteresis_band")
