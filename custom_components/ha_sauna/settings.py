"""Gemeinsamer, serialisierter Schreibweg für Parameter aller Bedienoberflächen."""

from collections.abc import Mapping
from dataclasses import replace

from .core.parameters import BY_KEY, LIVE_TEMPERATURE_KEYS, ParameterError, Parameters
from .core.program_catalog import load_programs, validate_programs
from .core.temperature_program import temperature_steps as validate_temperature_steps
from .presentation import parameter_error


class ConfigurationLocked(ValueError):
    """Eine Änderung würde die laufende Sitzung neu initialisieren."""


PROGRAM_PROFILES = frozenset(("constant", "progressive", "program_1", "program_2"))


def program_parameters(parameters, profile, *, catalog=None):
    """Return the live values and controller mode for an explicit profile.

    This is deliberately independent of Home Assistant so that the physical
    switch can make the same choice while it already holds the runtime lock.
    """
    catalog_by_id = {program.id: program for program in catalog or ()}
    allowed_profiles = (
        {"constant", "progressive"} | set(catalog_by_id)
        if catalog is not None
        else PROGRAM_PROFILES
    )
    if profile not in allowed_profiles:
        raise ValueError("Ungültiges Temperaturprogramm")
    values = parameters.as_dict()
    if profile in ("constant", "progressive"):
        return Parameters(values), profile
    if profile in catalog_by_id:
        program = catalog_by_id[profile]
        values.update(
            {
                "target_temperature_c": program.start_c,
                "final_temperature_c": program.end_c,
                "temperature_gangs": program.distribution_gangs,
            }
        )
    else:
        values.update(
            {
                "target_temperature_c": values[f"{profile}_start_c"],
                "final_temperature_c": values[f"{profile}_end_c"],
                "temperature_gangs": values[f"{profile}_gangs"],
            }
        )
    return Parameters(values), "progressive"


async def async_set_parameters(
    hass,
    entry,
    values,
    *,
    partial=False,
    explicit_target=None,
    program_mode=None,
    new_program=False,
):
    runtime = entry.runtime_data
    async with runtime._lock:
        return await _async_set_parameters_locked(
            hass,
            entry,
            values,
            partial=partial,
            explicit_target=explicit_target,
            program_mode=program_mode,
            new_program=new_program,
        )


async def async_reset_parameters(hass, entry):
    """Restore software settings while retaining physical entity associations."""
    runtime = entry.runtime_data
    async with runtime._lock:
        # Import here because Runtime imports ``program_parameters`` from this
        # module.  Configuration owns the defaults for the non-parameter UI
        # preferences while retaining the configured hardware bindings.
        from .runtime import Configuration

        runtime._require_open()
        if runtime.reconfiguring:
            raise ConfigurationLocked(
                "Die Grundeinstellungen werden gerade übernommen. Bitte kurz warten."
            )
        if runtime.session:
            raise ConfigurationLocked(
                "Einstellungen können erst nach Ende der Saunasitzung "
                "zurückgesetzt werden."
            )
        defaults = Configuration(runtime.configuration.bindings, Parameters({}))
        reset = replace(
            defaults,
            control_input_mode=runtime.configuration.control_input_mode,
            button_event_type=runtime.configuration.button_event_type,
        )
        # Ohne Änderung läuft kein Listener. Andernfalls hebt dieser die Sperre
        # nach direkter Übernahme oder durch das Neuladen der Laufzeit auf.
        runtime.reconfiguring = hass.config_entries.async_update_entry(
            entry, options=reset.as_options()
        )
        return reset.parameters.as_dict()


async def _async_set_parameters_locked(
    hass,
    entry,
    values,
    *,
    partial=False,
    explicit_target=None,
    program_mode=None,
    new_program=False,
):
    """Shared parameter write path; the caller holds ``runtime._lock``."""
    runtime = entry.runtime_data
    runtime._require_open()
    if runtime.reconfiguring:
        raise ConfigurationLocked(
            "Die Grundeinstellungen werden gerade übernommen. Bitte kurz warten."
        )
    if not isinstance(values, Mapping):
        raise ParameterError("base", "invalid_parameters")
    before = runtime.configuration.parameters.as_dict()
    merged = {**before, **values} if partial else dict(values)
    # Ein leer übermittelter Endwert fällt auf den zentralen Standard zurück.
    if partial and merged.get("final_temperature_c", False) is None:
        merged.pop("final_temperature_c")
    parameters = Parameters(merged)
    button_temperature = runtime.configuration.button_temperature_c
    if not (
        parameters.minimum_for("target_temperature_c")
        <= button_temperature
        <= BY_KEY["target_temperature_c"].maximum
    ):
        raise ParameterError("sauna_min_temperature_c", "button_temperature_invalid")
    # A changed lower bound must be valid for the whole stored catalog before
    # options are written; otherwise the next reload would reject saved data.
    try:
        validate_programs(
            runtime.configuration.temperature_programs,
            minimum_c=parameters.minimum_for("target_temperature_c"),
            maximum_c=BY_KEY["target_temperature_c"].maximum,
            maximum_gangs=BY_KEY["temperature_gangs"].maximum,
        )
    except ValueError as error:
        raise ParameterError(
            "sauna_min_temperature_c", "program_catalog_invalid"
        ) from error
    changed = {
        k
        for k in before.keys() | parameters.values.keys()
        if before.get(k) != parameters.values.get(k)
    }
    if runtime.session and changed - LIVE_TEMPERATURE_KEYS:
        raise ConfigurationLocked(
            "Während einer Saunasitzung sind nur Solltemperatur, Steigerungsverteilung und Endtemperatur änderbar. Andere Einstellungen gelten nach Ende der Sitzung."
        )
    explicit_target = (
        "target_temperature_c" in values if explicit_target is None else explicit_target
    )
    selected_mode = (
        program_mode
        if program_mode is not None
        else "constant"
        if explicit_target
        else None
    )
    # Re-sending the existing progressive mode for an end/count edit must
    # not turn it into a fresh program: that would discard its live anchor.
    if selected_mode == runtime.configuration.program_mode and not new_program:
        selected_mode = None
    mode_changed = (
        selected_mode is not None
        and selected_mode != runtime.configuration.program_mode
    )
    clear_selected_program = bool(set(values) & LIVE_TEMPERATURE_KEYS)
    # The legacy start/end/count form is always the evenly distributed form.
    # Passing ``None`` explicitly also lets a live end edit retain its current
    # target as the new anchor instead of continuing an old explicit list.
    selected_steps = None if clear_selected_program else ...
    if not changed - LIVE_TEMPERATURE_KEYS:
        await apply_temperature_parameters(
            runtime,
            parameters,
            explicit_target=explicit_target,
            # Jede direkte Sollwahl beendet ein laufendes Programm auch
            # dauerhaft; der Controller allein speichert diesen Standard
            # außerhalb einer Sitzung nicht.
            program_mode=selected_mode,
            new_program=new_program or mode_changed,
            selected_program_id=(
                None
                if clear_selected_program
                else runtime.configuration.selected_program_id
            ),
            temperature_steps=selected_steps,
        )
    else:
        runtime.reconfiguring = True
    # Bei weiteren Änderungen ohne Sitzung übernimmt der HA-Optionslistener
    # das Neuladen. Temperaturänderungen sind bereits vollständig angewendet.
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            "parameters": parameters.as_dict(),
            **({"program_mode": selected_mode} if selected_mode is not None else {}),
            **({"selected_program_id": None} if clear_selected_program else {}),
            **(
                {"temperature_steps": runtime.configuration.temperature_steps}
                if selected_steps is not ...
                else {}
            ),
        },
    )
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
            raise ConfigurationLocked(
                "Die Grundeinstellungen werden gerade übernommen. Bitte kurz warten."
            )
        parameters, mode = program_parameters(
            runtime.configuration.parameters,
            profile,
            catalog=runtime.configuration.temperature_programs,
        )
        if mode == "constant" and runtime.controller.target_temperature is not None:
            parameters = Parameters(
                {
                    **parameters.as_dict(),
                    "target_temperature_c": runtime.controller.target_temperature,
                }
            )
        await apply_temperature_parameters(
            runtime,
            parameters,
            program_mode=mode,
            new_program=True,
            temperature_steps=(
                next(
                    (
                        program.temperature_steps
                        for program in runtime.configuration.temperature_programs
                        if program.id == profile
                    ),
                    None,
                )
            ),
            selected_program_id=(
                profile
                if profile in {p.id for p in runtime.configuration.temperature_programs}
                else None
            ),
        )
        hass.config_entries.async_update_entry(
            entry,
            options={
                **entry.options,
                "parameters": parameters.as_dict(),
                "program_mode": mode,
                "selected_program_id": (
                    profile
                    if profile
                    in {p.id for p in runtime.configuration.temperature_programs}
                    else None
                ),
                "temperature_steps": runtime.configuration.temperature_steps,
            },
        )
        return parameters.as_dict()


async def apply_temperature_parameters(
    runtime,
    parameters,
    *,
    explicit_target=False,
    program_mode=None,
    new_program=False,
    selected_program_id=...,
    temperature_steps=...,
):
    """Aufrufer hält runtime._lock; niemals Sitzung oder Regelzustände ersetzen."""
    before = runtime.configuration.parameters.as_dict()
    mode_before = runtime.configuration.program_mode
    target_before = runtime.controller.target_temperature
    runtime.controller.update_temperature_parameters(
        parameters,
        runtime._clock(),
        explicit_target=explicit_target,
        program_mode=program_mode,
        new_program=new_program,
        temperature_steps=temperature_steps,
    )
    # Eine gerade abgelaufene Sitzung erhält ihren bisherigen Parameterstand.
    runtime.persist_completed_sessions()
    runtime.configuration = replace(
        runtime.configuration,
        parameters=parameters,
        program_mode=program_mode
        if program_mode is not None
        else runtime.configuration.program_mode,
        selected_program_id=(
            runtime.configuration.selected_program_id
            if selected_program_id is ...
            else selected_program_id
        ),
        temperature_steps=(
            runtime.configuration.temperature_steps
            if temperature_steps is ...
            else temperature_steps
        ),
    )
    if runtime.device:
        runtime.device.values = parameters.values
    after = parameters.as_dict()
    target_after = runtime.controller.target_temperature
    changes = {
        k: {"before": before.get(k), "after": after.get(k)}
        for k in before.keys() | after.keys()
        if before.get(k) != after.get(k)
    }
    if (
        changes
        or target_before != target_after
        or mode_before != runtime.configuration.program_mode
    ):
        runtime.log.info(
            "temperature_settings",
            "Temperaturprogramm geändert; Solltemperatur jetzt %s °C. Laufende Fristen bleiben erhalten.",
            target_after,
        )
        if runtime.archive:
            runtime.archive.append(
                "parameter_change",
                runtime._clock(),
                {
                    "changes": changes,
                    "mode_before": mode_before,
                    "mode_after": runtime.configuration.program_mode,
                    "target_before": target_before,
                    "target_after": target_after,
                },
                runtime.session.session_id if runtime.session else None,
            )
        runtime._archive_signature = None
    await runtime._cycle()


async def async_set_button_program(hass, entry, profile, temperature_c=None):
    """Atomically persist the physical button's named or constant program."""
    runtime = entry.runtime_data
    async with runtime._lock:
        runtime._require_open()
        if runtime.reconfiguring or runtime.session:
            raise ConfigurationLocked(
                "Das Tasterprogramm kann gerade nicht geändert werden."
            )
        ids = {program.id for program in runtime.configuration.temperature_programs}
        if (
            not isinstance(profile, str)
            or profile not in {"constant", *ids}
            or (profile != "constant" and temperature_c is not None)
        ):
            raise ValueError("Ungültiges Tasterprogramm")
        temperature = runtime.configuration.button_temperature_c
        if profile == "constant" and temperature_c is not None:
            temperature = Parameters(
                {
                    **runtime.configuration.parameters.as_dict(),
                    "target_temperature_c": temperature_c,
                }
            ).values["target_temperature_c"]
        configuration = replace(
            runtime.configuration,
            button_program=profile,
            button_temperature_c=temperature,
        )
        # This selection has no effect on an already-running controller (which
        # is excluded above), so it can be adopted immediately instead of
        # waiting for the options listener to reload the runtime.
        runtime.configuration = configuration
        hass.config_entries.async_update_entry(
            entry, options=configuration.as_options()
        )
        return configuration


async def async_set_program_catalog(hass, entry, stored):
    """Replace the complete catalog atomically while no sauna session exists."""
    runtime = entry.runtime_data
    async with runtime._lock:
        runtime._require_open()
        if runtime.reconfiguring:
            raise ConfigurationLocked(
                "Die Grundeinstellungen werden gerade übernommen. Bitte kurz warten."
            )
        if runtime.session:
            raise ConfigurationLocked(
                "Temperaturprogramme können erst nach Ende der Saunasitzung "
                "geändert werden."
            )
        parameters = runtime.configuration.parameters
        try:
            programs = load_programs(
                stored,
                minimum_c=parameters.minimum_for("target_temperature_c"),
                maximum_c=BY_KEY["target_temperature_c"].maximum,
                maximum_gangs=BY_KEY["temperature_gangs"].maximum,
            )
        except ValueError as error:
            raise ValueError(f"Ungültiger Temperaturprogrammkatalog: {error}") from None
        ids = {program.id for program in programs}
        if (
            runtime.configuration.button_program not in {"current", "constant"}
            and runtime.configuration.button_program not in ids
        ):
            raise ValueError(
                "Das für den Taster ausgewählte Temperaturprogramm darf nicht "
                "gelöscht werden."
            )
        selected = runtime.configuration.selected_program_id
        if selected is not None and selected not in ids:
            raise ValueError(
                "Das ausgewählte Temperaturprogramm darf nicht gelöscht werden."
            )
        previous = next(
            (
                program
                for program in runtime.configuration.temperature_programs
                if program.id == selected
            ),
            None,
        )
        selected_program = next(
            (program for program in programs if program.id == selected), None
        )
        changed_values = selected is not None and (
            previous is None
            or (
                previous.start_c,
                previous.end_c,
                previous.distribution_gangs,
                previous.temperature_steps,
            )
            != (
                selected_program.start_c,
                selected_program.end_c,
                selected_program.distribution_gangs,
                selected_program.temperature_steps,
            )
        )
        if changed_values:
            selected_parameters, mode = program_parameters(
                parameters, selected, catalog=programs
            )
            await apply_temperature_parameters(
                runtime,
                selected_parameters,
                program_mode=mode,
                new_program=True,
                selected_program_id=selected,
                temperature_steps=selected_program.temperature_steps,
            )
        runtime.configuration = replace(
            runtime.configuration,
            temperature_programs=programs,
            selected_program_id=selected,
        )
        hass.config_entries.async_update_entry(
            entry, options=runtime.configuration.as_options()
        )
        return [program.as_dict() for program in programs]


async def async_set_temperature_steps(hass, entry, values):
    """Select explicitly entered stages through the same controller path."""
    runtime = entry.runtime_data
    async with runtime._lock:
        runtime._require_open()
        if runtime.reconfiguring:
            raise ConfigurationLocked(
                "Die Grundeinstellungen werden gerade übernommen. Bitte kurz warten."
            )
        steps = validate_temperature_steps(values, "temperature_steps")
        maximum = int(BY_KEY["temperature_gangs"].maximum)
        minimum = runtime.configuration.parameters.minimum_for("target_temperature_c")
        if len(steps) > maximum or any(
            step < minimum or step > BY_KEY["target_temperature_c"].maximum
            for step in steps
        ):
            raise ParameterError("base", "invalid_parameters")
        parameters = Parameters(
            {
                **runtime.configuration.parameters.as_dict(),
                "target_temperature_c": steps[0],
                "final_temperature_c": steps[-1],
                "temperature_gangs": len(steps),
            }
        )
        await apply_temperature_parameters(
            runtime,
            parameters,
            program_mode="progressive",
            new_program=True,
            selected_program_id=None,
            temperature_steps=steps,
        )
        hass.config_entries.async_update_entry(
            entry, options=runtime.configuration.as_options()
        )
        return parameters.as_dict()


async def async_set_control_mode(hass, entry, mode):
    """Switch controller mode and save the matching configuration atomically."""
    runtime = entry.runtime_data
    async with runtime._lock:
        runtime._require_open()
        if runtime.reconfiguring:
            raise ConfigurationLocked(
                "Die Grundeinstellungen werden gerade übernommen. Bitte kurz warten."
            )
        if not isinstance(mode, str) or mode not in {"automatic", "manual"}:
            raise ValueError("Ungültiger Betriebsmodus")
        runtime.controller.set_control_mode(mode)
        runtime.configuration = replace(runtime.configuration, control_mode=mode)
        hass.config_entries.async_update_entry(
            entry, options=runtime.configuration.as_options()
        )
        await runtime._cycle()


async def async_set_entity_parameter(hass, entry, key, value):
    try:
        await async_set_parameters(hass, entry, {key: value}, partial=True)
    except ParameterError as error:
        raise ValueError(parameter_error(error)) from None
