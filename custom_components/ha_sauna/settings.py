"""Gemeinsamer, serialisierter Schreibweg für Parameter aller Bedienoberflächen."""
from collections.abc import Mapping
from dataclasses import replace

from .core.parameters import LIVE_TEMPERATURE_KEYS, Parameters, ParameterError
from .presentation import parameter_error


class ConfigurationLocked(ValueError):
    """Eine Änderung würde die laufende Sitzung neu initialisieren."""


PROGRAM_PROFILES = frozenset(("constant", "progressive", "program_1", "program_2"))


def program_parameters(parameters, profile):
    """Return the live values and controller mode for an explicit profile.

    This is deliberately independent of Home Assistant so that the physical
    switch can make the same choice while it already holds the runtime lock.
    """
    if profile not in PROGRAM_PROFILES:
        raise ValueError("Ungültiges Temperaturprogramm")
    values = parameters.as_dict()
    if profile in ("constant", "progressive"):
        return Parameters(values), profile
    values.update({
        "target_temperature_c": values[f"{profile}_start_c"],
        "final_temperature_c": values[f"{profile}_end_c"],
        "temperature_gangs": values[f"{profile}_gangs"],
    })
    return Parameters(values), "progressive"


async def async_set_parameters(hass, entry, values, *, partial=False,
                               explicit_target=None, program_mode=None,
                               new_program=False):
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
            raise ConfigurationLocked("Während einer Saunasitzung sind nur Solltemperatur, Steigerungsverteilung und Endtemperatur änderbar. Andere Einstellungen gelten nach Ende der Sitzung.")
        explicit_target = ("target_temperature_c" in values
                           if explicit_target is None else explicit_target)
        selected_mode = (program_mode if program_mode is not None else "constant"
                         if explicit_target else None)
        # Re-sending the existing progressive mode for an end/count edit must
        # not turn it into a fresh program: that would discard its live anchor.
        if selected_mode == runtime.configuration.program_mode and not new_program:
            selected_mode = None
        mode_changed = selected_mode is not None and selected_mode != runtime.configuration.program_mode
        if not changed - LIVE_TEMPERATURE_KEYS:
            await apply_temperature_parameters(runtime, parameters,
                explicit_target=explicit_target,
                # Jede direkte Sollwahl beendet ein laufendes Programm auch
                # dauerhaft; der Controller allein speichert diesen Standard
                # außerhalb einer Sitzung nicht.
                program_mode=selected_mode, new_program=new_program or mode_changed)
        else:
            runtime.reconfiguring = True
        # Bei weiteren Änderungen ohne Sitzung übernimmt der HA-Optionslistener
        # das Neuladen. Temperaturänderungen sind bereits vollständig angewendet.
        hass.config_entries.async_update_entry(entry, options={
            **entry.options, "parameters": parameters.as_dict(),
            **({"program_mode": selected_mode} if selected_mode is not None else {})})
        return parameters.as_dict()


async def async_set_program(hass, entry, profile):
    """Select a constant, free progressive, or stored program without a reload.

    ``progressive`` uses the current start, end and distribution values; a
    stored profile contributes its own start, end and distribution count. Its
    mode and values are persisted so a Home Assistant restart continues with
    exactly the selected choice.
    """
    runtime = entry.runtime_data
    async with runtime._lock:
        runtime._require_open()
        if runtime.reconfiguring:
            raise ConfigurationLocked("Die Grundeinstellungen werden gerade übernommen. Bitte kurz warten.")
        parameters, mode = program_parameters(runtime.configuration.parameters, profile)
        if mode == "constant" and runtime.controller.target_temperature is not None:
            parameters = Parameters({**parameters.as_dict(),
                "target_temperature_c": runtime.controller.target_temperature})
        await apply_temperature_parameters(runtime, parameters,
            program_mode=mode, new_program=True)
        hass.config_entries.async_update_entry(entry, options={
            **entry.options, "parameters": parameters.as_dict(), "program_mode": mode,
        })
        return parameters.as_dict()


async def apply_temperature_parameters(runtime, parameters, *, explicit_target=False,
                                       program_mode=None, new_program=False):
    """Aufrufer hält runtime._lock; niemals Sitzung oder Regelzustände ersetzen."""
    before = runtime.configuration.parameters.as_dict()
    mode_before = runtime.configuration.program_mode
    target_before = runtime.controller.target_temperature
    runtime.controller.update_temperature_parameters(parameters, runtime._clock(),
        explicit_target=explicit_target, program_mode=program_mode,
        new_program=new_program)
    # Eine gerade abgelaufene Sitzung erhält ihren bisherigen Parameterstand.
    runtime.persist_completed_sessions()
    runtime.configuration = replace(runtime.configuration, parameters=parameters,
        program_mode=program_mode if program_mode is not None else runtime.configuration.program_mode)
    if runtime.device:
        runtime.device.values = parameters.values
    after = parameters.as_dict()
    target_after = runtime.controller.target_temperature
    changes = {k: {"before": before.get(k), "after": after.get(k)}
               for k in before.keys() | after.keys() if before.get(k) != after.get(k)}
    if changes or target_before != target_after or mode_before != runtime.configuration.program_mode:
        runtime.log.info("temperature_settings", "Temperaturprogramm geändert; Solltemperatur jetzt %s °C. Laufende Fristen bleiben erhalten.", target_after)
        if runtime.archive:
            runtime.archive.append("parameter_change", runtime._clock(), {
                "changes": changes, "mode_before": mode_before,
                "mode_after": runtime.configuration.program_mode,
                "target_before": target_before, "target_after": target_after,
            }, runtime.session.session_id if runtime.session else None)
        runtime._archive_signature = None
    await runtime._cycle()


async def async_set_entity_parameter(hass, entry, key, value):
    try:
        await async_set_parameters(hass, entry, {key: value}, partial=True)
    except ParameterError as error:
        raise ValueError(parameter_error(error)) from None
