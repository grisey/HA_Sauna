"""Gemeinsamer, serialisierter Schreibweg für Parameter aller Bedienoberflächen."""
from collections.abc import Mapping
from dataclasses import replace

from .core.parameters import LIVE_TEMPERATURE_KEYS, Parameters, ParameterError
from .presentation import parameter_error


class ConfigurationLocked(ValueError):
    """Eine Änderung würde die laufende Sitzung neu initialisieren."""


async def async_set_parameters(hass, entry, values, *, partial=False):
    runtime = entry.runtime_data
    async with runtime._lock:
        runtime._require_open()
        if runtime.reconfiguring:
            raise ConfigurationLocked("Die Grundeinstellungen werden gerade übernommen. Bitte kurz warten.")
        if not isinstance(values, Mapping):
            raise ParameterError("base", "invalid_parameters")
        before = runtime.configuration.parameters.as_dict()
        merged = {**before, **values} if partial else dict(values)
        # Optionalen Endwert in einer Teiländerung ausdrücklich entfernen.
        if partial and merged.get("final_temperature_c", False) is None:
            merged.pop("final_temperature_c")
        parameters = Parameters(merged)
        changed = {k for k in before.keys() | parameters.values.keys()
                   if before.get(k) != parameters.values.get(k)}
        if runtime.session and changed - LIVE_TEMPERATURE_KEYS:
            raise ConfigurationLocked("Während einer Saunasitzung sind nur Solltemperatur, Steigerungsrate und Endtemperatur änderbar. Andere Einstellungen gelten nach Ende der Sitzung.")
        if not changed - LIVE_TEMPERATURE_KEYS:
            await apply_temperature_parameters(runtime, parameters,
                explicit_target="target_temperature_c" in values)
        else:
            runtime.reconfiguring = True
        # Bei weiteren Änderungen ohne Sitzung übernimmt der HA-Optionslistener
        # das Neuladen. Temperaturänderungen sind bereits vollständig angewendet.
        hass.config_entries.async_update_entry(entry, options={
            **entry.options, "parameters": parameters.as_dict()})
        return parameters.as_dict()


async def apply_temperature_parameters(runtime, parameters, *, explicit_target=False):
    """Aufrufer hält runtime._lock; niemals Sitzung oder Regelzustände ersetzen."""
    before = runtime.configuration.parameters.as_dict()
    target_before = runtime.controller.target_temperature
    runtime.controller.update_temperature_parameters(parameters, runtime._clock(),
        explicit_target=explicit_target)
    # Eine gerade abgelaufene Sitzung erhält ihren bisherigen Parameterstand.
    runtime.persist_completed_sessions()
    runtime.configuration = replace(runtime.configuration, parameters=parameters)
    if runtime.device:
        runtime.device.values = parameters.values
    after = parameters.as_dict()
    target_after = runtime.controller.target_temperature
    changes = {k: {"before": before.get(k), "after": after.get(k)}
               for k in before.keys() | after.keys() if before.get(k) != after.get(k)}
    if changes or target_before != target_after:
        runtime.log.info("temperature_settings", "Temperaturprogramm geändert; Solltemperatur jetzt %s °C. Laufende Fristen bleiben erhalten.", target_after)
        if runtime.archive:
            runtime.archive.append("parameter_change", runtime._clock(), {
                "changes": changes, "target_before": target_before, "target_after": target_after,
            }, runtime.session.session_id if runtime.session else None)
        runtime._archive_signature = None
    await runtime._cycle()


async def async_set_entity_parameter(hass, entry, key, value):
    try:
        await async_set_parameters(hass, entry, {key: value}, partial=True)
    except ParameterError as error:
        raise ValueError(parameter_error(error)) from None
