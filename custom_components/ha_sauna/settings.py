"""Gemeinsamer, serialisierter Schreibweg für Parameter aller Bedienoberflächen."""

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from hashlib import sha256

from .appearance import validate_appearance
from .core.button import validate_session_gesture
from .core.configuration_model import ConfigurationModel, temperature_control
from .core.parameters import BY_KEY, LIVE_TEMPERATURE_KEYS, ParameterError, Parameters
from .core.program_catalog import load_programs, validate_programs
from .core.temperature_program import temperature_steps as validate_temperature_steps
from .presentation import parameter_error


class ConfigurationLocked(ValueError):
    """Eine Änderung würde die laufende Sitzung neu initialisieren."""


class StaleFieldStandardPreview(ValueError):
    """The current field sources require another explicit confirmation."""

    def __init__(self, preview):
        self.preview = preview
        super().__init__(
            "Die betroffenen Einstellungen haben sich geändert. "
            "Bitte die aktuelle Vorschau erneut bestätigen."
        )


def field_standard_action_arguments(body):
    """Check transport keys and types; settings owns action and field semantics."""
    if (
        not isinstance(body, dict)
        or "action" not in body
        or set(body) - {"action", "field", "group", "all", "value", "confirmation"}
    ):
        raise ValueError("Ungültige Standardaktion")
    for key in ("action", "field", "group", "confirmation"):
        if key in body and (not isinstance(body[key], str) or not body[key]):
            raise ValueError(f"{key} muss eine nicht leere Zeichenfolge sein")
    if "all" in body and body["all"] is not True:
        raise ValueError("all muss wahr sein")
    return {
        key: value
        for key, value in body.items()
        if key not in {"action", "confirmation"}
    }


_NO_FIELD_VALUE = object()


def _require_field_standard_runtime(entry, runtime):
    runtime._require_open()
    if entry.runtime_data is not runtime or runtime.reconfiguring:
        raise ConfigurationLocked(
            "Die Grundeinstellungen werden gerade übernommen. Bitte kurz warten."
        )


def _field_standard_values(configuration, fields):
    metadata = configuration.standard_field_metadata()
    return [
        {
            "id": name,
            **metadata[name],
            "factory": configuration.field_value(name, source="factory"),
            "standard": configuration.field_value(name, source="standard"),
            "active": configuration.field_value(name),
            "has_standard": name in configuration.field_standards,
        }
        for name in fields
    ]


def _field_standard_action_context(
    configuration, action, *, field=None, group=None, all=False,
    value=_NO_FIELD_VALUE,
):
    """Resolve the permanent scope and sign only current action dependencies."""
    if not isinstance(action, str) or action not in {
        "overwrite_standard", "restore_standard", "restore_factory"
    }:
        raise ValueError("Ungültige Standardaktion")
    if (
        not isinstance(all, bool)
        or sum((field is not None, group is not None, all)) != 1
    ):
        raise ValueError("Genau ein Feld, Bereich oder Gesamtumfang ist erforderlich")
    if action == "overwrite_standard":
        if field is None or value is _NO_FIELD_VALUE:
            raise ValueError(
                "Ein Standard kann nur für ein einzelnes Feld gesetzt werden"
            )
    elif value is not _NO_FIELD_VALUE:
        raise ValueError("Rücksetzen übernimmt keine Entwurfswerte")
    names = configuration.standard_fields(field=field, group=group)
    scope = {"field": field} if field is not None else (
        {"group": group} if group is not None else {"all": True}
    )
    fields = _field_standard_values(configuration, names)
    factory = action == "restore_factory"
    overwrite = action == "overwrite_standard"
    for row in fields:
        row.update({
            "target": row["active"] if overwrite else (
                row["factory"] if factory else row["standard"]
            ),
            "target_standard": value if overwrite else (
                row["factory"] if factory else row["standard"]
            ),
            "target_has_standard": True if overwrite else (
                False if factory else row["has_standard"]
            ),
        })
    validation = {}
    if any(name.startswith("parameters.") for name in names):
        # Full parameter validation includes cross-field relationships and the
        # retained catalog/stages. Unrelated appearance is no dependency.
        validation = {
            "parameters": configuration.parameters.as_dict(),
            "temperature_programs": [
                program.as_dict() for program in configuration.temperature_programs
            ],
            "temperature_steps": configuration.temperature_steps,
        }
    scales = {
        name.split(".")[2] for name in names
        if name.startswith("appearance.scales.")
    }
    if scales:
        validation["scales"] = {
            name: dict(configuration.appearance["scales"][name])
            for name in sorted(scales)
        }
    preview = {"action": action, "scope": scope, "fields": fields}
    try:
        signed = json.dumps(
            {**preview, "validation": validation},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Ungültiger Feldstandard") from None
    preview["confirmation"] = sha256(signed.encode("utf-8")).hexdigest()
    return names, preview


def _field_standard_candidate(configuration, action, names, value):
    if action == "overwrite_standard":
        name = names[0]
        draft = configuration.with_field_values({name: value})
        return configuration.with_field_standard(name, draft.field_value(name))
    source = "factory" if action == "restore_factory" else "standard"
    candidate = configuration.with_field_values({
        name: configuration.field_value(name, source=source) for name in names
    })
    return (
        candidate.without_field_standards(names)
        if action == "restore_factory" else candidate
    )


def _field_standard_targets(preview, candidate):
    for row in preview["fields"]:
        name = row["id"]
        row.update({
            "target": candidate.field_value(name),
            "target_standard": candidate.field_value(name, source="standard"),
            "target_has_standard": name in candidate.field_standards,
        })


async def async_get_field_standards(entry):
    """Read the complete W/S/A inventory, including hidden permanent fields."""
    runtime = entry.runtime_data
    async with runtime._lock:
        _require_field_standard_runtime(entry, runtime)
        configuration = runtime.configuration
        return {"fields": _field_standard_values(
            configuration, configuration.standard_fields()
        )}


async def async_preview_field_standard_action(
    entry, action, *, field=None, group=None, all=False, value=_NO_FIELD_VALUE,
):
    """Return a validated proposal without advancing or changing the runtime."""
    runtime = entry.runtime_data
    # serialized() drains input and delivers detection before yielding. The
    # shared lock alone keeps this inventory/proposal strictly read-only.
    async with runtime._lock:
        _require_field_standard_runtime(entry, runtime)
        configuration = runtime.configuration
        names, preview = _field_standard_action_context(
            configuration, action, field=field, group=group, all=all, value=value,
        )
        candidate = _field_standard_candidate(configuration, action, names, value)
        _field_standard_targets(preview, candidate)
        return preview


async def async_apply_field_standard_action(
    hass, entry, action, *, confirmation, field=None, group=None, all=False,
    value=_NO_FIELD_VALUE, actor=None,
):
    """Apply exactly the current confirmed proposal as one configuration write."""
    runtime = entry.runtime_data
    async with runtime._lock:
        _require_field_standard_runtime(entry, runtime)
        if not isinstance(confirmation, str) or not confirmation:
            raise ValueError("Ungültige Standardbestätigung")
        # A persisted options update may be waiting behind this lock. Never
        # overwrite that newer source with an older live Configuration.
        from .runtime import Configuration

        configuration = runtime.configuration
        if (
            Configuration.from_options(entry.options).as_options()
            != configuration.as_options()
        ):
            raise ConfigurationLocked(
                "Die Grundeinstellungen werden gerade übernommen. Bitte kurz warten."
            )
        names, preview = _field_standard_action_context(
            configuration, action, field=field, group=group, all=all, value=value,
        )
        stale = confirmation != preview["confirmation"]
        try:
            candidate = _field_standard_candidate(configuration, action, names, value)
        except ValueError as error:
            if not stale:
                raise
            preview["validation_error"] = (
                parameter_error(error) if isinstance(error, ParameterError)
                else str(error)
            )
            preview["confirmation"] = None
            raise StaleFieldStandardPreview(preview) from None
        _field_standard_targets(preview, candidate)
        if stale:
            raise StaleFieldStandardPreview(preview)
        parameters_changed = (
            candidate.parameters.as_dict() != configuration.parameters.as_dict()
        )
        if runtime.session and parameters_changed:
            raise ConfigurationLocked(
                "Während einer Saunasitzung sind nur Solltemperatur, "
                "Steigerungsverteilung und Endtemperatur änderbar. "
                "Andere Einstellungen gelten nach Ende der Sitzung."
            )
        # Everything, including serialization, is checked before the first
        # write. Parameter restores retain the existing options-listener path;
        # S-only and appearance changes have no controller or archive effects.
        options = candidate.as_options()
        if not parameters_changed:
            runtime.configuration = candidate
        try:
            changed = hass.config_entries.async_update_entry(entry, options=options)
        except BaseException:
            runtime.configuration = configuration
            raise
        if parameters_changed:
            runtime.reconfiguring = changed
        elif candidate.appearance != configuration.appearance:
            runtime.notify_entities()
        return {**preview, "applied": True}


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
    if profile == "constant":
        values["target_temperature_c"] = values["standard_temperature_c"]
        return Parameters(values), profile
    if profile == "progressive":
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
    actor=None,
):
    runtime = entry.runtime_data
    async with runtime.serialized():
        return await _async_set_parameters_locked(
            hass,
            entry,
            values,
            partial=partial,
            explicit_target=explicit_target,
            program_mode=program_mode,
            new_program=new_program,
            actor=actor,
        )


async def async_reset_parameters(hass, entry):
    """Restore software settings while retaining physical entity associations."""
    runtime = entry.runtime_data
    async with runtime.serialized():
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
            presence_source=runtime.configuration.presence_source,
            appearance=runtime.configuration.appearance,
            field_standards=runtime.configuration.field_standards,
            notification_assignments=runtime.configuration.notification_assignments,
        )
        # Ohne Änderung läuft kein Listener. Andernfalls hebt dieser die Sperre
        # nach direkter Übernahme oder durch das Neuladen der Laufzeit auf.
        runtime.reconfiguring = hass.config_entries.async_update_entry(
            entry, options=reset.as_options()
        )
        return reset.parameters.as_dict()


async def async_set_appearance(hass, entry, value):
    """Persist only display preferences without cycling the active controller."""
    appearance = validate_appearance(value, reject_catalog_overrides=True)
    runtime = entry.runtime_data
    async with runtime._lock:
        runtime._require_open()
        if runtime.reconfiguring:
            raise ConfigurationLocked(
                "Die Grundeinstellungen werden gerade übernommen. Bitte kurz warten."
            )
        runtime.configuration = replace(runtime.configuration, appearance=appearance)
        hass.config_entries.async_update_entry(
            entry, options=runtime.configuration.as_options()
        )
        runtime.notify_entities()
        return appearance


@dataclass(frozen=True)
class ParameterChange:
    """One validated edit, shared by loaded and unloaded options paths."""

    configuration: object
    changed: frozenset[str]
    selected_mode: str | None
    explicit_target: bool
    clear_program: bool


def parameter_change(
    configuration,
    values,
    *,
    partial=False,
    explicit_target=None,
    program_mode=None,
    new_program=False,
):
    """Build the complete candidate before a live or offline edit is saved."""
    if not isinstance(values, Mapping):
        raise ParameterError("base", "invalid_parameters")
    before = configuration.parameters.as_dict()
    for key, value in values.items():
        definition = BY_KEY.get(key)
        if (
            definition is not None and definition.configuration_surface == "catalog"
            and value != before.get(key)
        ):
            raise ParameterError(key, "catalog_only")
    merged = {**before, **values} if partial else dict(values)
    # Ein leer übermittelter Endwert fällt auf den zentralen Standard zurück.
    if partial and merged.get("final_temperature_c", False) is None:
        merged.pop("final_temperature_c")
    parameters = Parameters(merged)
    # A changed lower bound must be valid for the whole stored catalog before
    # options are written; otherwise the next reload would reject saved data.
    try:
        validate_programs(
            configuration.temperature_programs,
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
    model = ConfigurationModel.from_configuration(configuration)
    for key in values if partial else sorted(changed):
        if not model.parameter_applicable(key):
            raise ParameterError(key, "not_applicable")
    explicit_target = (
        "target_temperature_c" in (values if partial else changed)
        if explicit_target is None
        else explicit_target
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
    if selected_mode == configuration.program_mode and not new_program:
        selected_mode = None
    clear_selected_program = bool(
        new_program
        or changed & LIVE_TEMPERATURE_KEYS
        or (explicit_target and "target_temperature_c" in values)
    )
    # The legacy start/end/count form is always the evenly distributed form.
    # Passing ``None`` explicitly also lets a live end edit retain its current
    # target as the new anchor instead of continuing an old explicit list.
    selected_steps = None if clear_selected_program else ...
    # Validate the resulting configuration, including free interior stages,
    # before either the controller or persisted options can change. A full
    # form carrying an unchanged target is not a new constant selection.
    try:
        candidate = replace(
            configuration,
            parameters=parameters,
            program_mode=selected_mode or configuration.program_mode,
            selected_program_id=(
                None if clear_selected_program else configuration.selected_program_id
            ),
            temperature_steps=(
                configuration.temperature_steps
                if selected_steps is ...
                else selected_steps
            ),
        )
    except ValueError as error:
        raise ParameterError("base", "invalid_parameters") from error
    return ParameterChange(
        candidate, frozenset(changed), selected_mode, explicit_target,
        clear_selected_program,
    )


async def _async_set_parameters_locked(
    hass,
    entry,
    values,
    *,
    partial=False,
    explicit_target=None,
    program_mode=None,
    new_program=False,
    actor=None,
):
    """Shared parameter write path; the caller holds ``runtime._lock``."""
    runtime = entry.runtime_data
    runtime._require_open()
    if runtime.reconfiguring:
        raise ConfigurationLocked(
            "Die Grundeinstellungen werden gerade übernommen. Bitte kurz warten."
        )
    if (
        partial and isinstance(values, Mapping)
        and set(values) == {"target_temperature_c"}
        and explicit_target is None and program_mode is None and not new_program
        and temperature_control(runtime.controller).scope == "next_gang"
    ):
        # A live instrument edit belongs to one upcoming gang, not to the
        # saved program. Validate through the same parameter bounds/rounding.
        parameters = Parameters({
            **runtime.configuration.parameters.as_dict(), **values,
        })
        now = runtime._clock()
        runtime.controller.advance(now, evaluate=False)
        runtime.persist_completed_sessions()
        if runtime.session:
            before = runtime.controller.next_gang_temperature
            program_before = _session_temperature_state(runtime)
            target = parameters.values["target_temperature_c"]
            runtime.controller.set_next_gang_temperature(target, now)
            runtime.log.info(
                "temperature_settings", "Temperatur für den nächsten Gang: %s °C.",
                target,
            )
            if runtime.archive:
                runtime.archive.append(
                    "parameter_change", now,
                    {
                        "next_gang_temperature_before": before,
                        "next_gang_temperature_after": target,
                        "target_after": runtime.controller.target_temperature,
                        "mode_after": runtime.configuration.program_mode,
                    },
                    runtime.session.session_id,
                )
            runtime._archive_signature = None
            await runtime._cycle()
            if program_before != _session_temperature_state(runtime):
                runtime._record_notification_participant(actor)
            return runtime.configuration.parameters.as_dict()
    change = parameter_change(
        runtime.configuration, values, partial=partial,
        explicit_target=explicit_target, program_mode=program_mode,
        new_program=new_program,
    )
    candidate, changed = change.configuration, change.changed
    parameters = candidate.parameters
    selected_mode, explicit_target = change.selected_mode, change.explicit_target
    clear_selected_program = change.clear_program
    selected_steps = None if clear_selected_program else ...
    if runtime.session and changed - LIVE_TEMPERATURE_KEYS:
        raise ConfigurationLocked(
            "Während einer Saunasitzung sind nur Solltemperatur, Steigerungsverteilung und Endtemperatur änderbar. Andere Einstellungen gelten nach Ende der Sitzung."
        )
    mode_changed = candidate.program_mode != runtime.configuration.program_mode
    if not changed and not mode_changed and not new_program and not explicit_target:
        return parameters.as_dict()
    if not changed - LIVE_TEMPERATURE_KEYS:
        await apply_temperature_parameters(
            runtime,
            parameters,
            explicit_target=explicit_target,
            program_mode=selected_mode,
            new_program=new_program or mode_changed,
            selected_program_id=(
                None
                if clear_selected_program
                else runtime.configuration.selected_program_id
            ),
            temperature_steps=selected_steps,
            actor=actor,
        )
    else:
        runtime.reconfiguring = True
    # Bei weiteren Änderungen ohne Sitzung übernimmt der HA-Optionslistener
    # das Neuladen. Temperaturänderungen sind bereits vollständig angewendet.
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            "parameters": parameters.as_options(),
            **({"program_mode": selected_mode} if selected_mode is not None else {}),
            **({"selected_program_id": None} if clear_selected_program else {}),
            **(
                {"temperature_steps": candidate.temperature_steps}
                if selected_steps is not ...
                else {}
            ),
        },
    )
    return parameters.as_dict()


async def async_set_program(hass, entry, profile, *, actor=None):
    """Select a constant, free progressive, or stored program without a reload.

    ``progressive`` uses the current start, end and distribution values; a
    stored profile contributes its own start, end and distribution count. Its
    mode and values are persisted so a Home Assistant restart continues with
    exactly the selected choice.
    """
    runtime = entry.runtime_data
    async with runtime.serialized():
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
        await apply_temperature_parameters(
            runtime,
            parameters,
            actor=actor,
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
                "parameters": parameters.as_options(),
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


def _session_temperature_state(runtime):
    """Only mutable program/override fields affected by temperature writes."""
    session = runtime.session
    if session is None:
        return None
    return tuple(getattr(session, key) for key in (
        "temperature_base_c", "temperature_base_gang_count",
        "temperature_program_mode", "temperature_program_gangs",
        "temperature_program_steps", "temperature_program_start_gang_count",
        "next_gang_temperature_c", "next_gang_temperature_blocked_by",
        "active_gang_temperature_c", "active_gang_temperature_id",
    ))


async def apply_temperature_parameters(
    runtime,
    parameters,
    *,
    explicit_target=False,
    program_mode=None,
    new_program=False,
    selected_program_id=...,
    temperature_steps=...,
    actor=None,
):
    """Aufrufer hält runtime._lock; niemals Sitzung oder Regelzustände ersetzen."""
    before = runtime.configuration.parameters.as_dict()
    program_before = _session_temperature_state(runtime)
    selected_before = runtime.configuration.selected_program_id
    steps_before = runtime.configuration.temperature_steps
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
    if (
        changes or target_before != target_after
        or mode_before != runtime.configuration.program_mode
        or selected_before != runtime.configuration.selected_program_id
        or steps_before != runtime.configuration.temperature_steps
        or program_before != _session_temperature_state(runtime)
    ):
        runtime._record_notification_participant(actor)


async def async_set_button_gesture(hass, entry, gesture):
    """Persist the common start/end gesture and discard any unfinished input."""
    runtime = entry.runtime_data
    async with runtime.serialized():
        runtime._require_open()
        if (
            runtime.reconfiguring or runtime.session
            or runtime._button_hold_session_id is not None
            or runtime._button_start_hold_session_id is not None
        ):
            raise ConfigurationLocked("Die Tastergeste kann gerade nicht geändert werden.")
        gesture = validate_session_gesture(gesture)
        if gesture not in runtime.available_button_session_gestures:
            raise ValueError("Der zugeordnete Taster meldet diese Sitzungsgeste nicht.")
        configuration = replace(runtime.configuration, button_session_gesture=gesture)
        runtime.configuration = configuration
        runtime._reset_button_gestures()
        hass.config_entries.async_update_entry(entry, options=configuration.as_options())
        await runtime._cycle()
        return configuration


async def async_set_button_program(hass, entry, profile):
    """Atomically persist the physical button's named or constant program."""
    runtime = entry.runtime_data
    async with runtime.serialized():
        runtime._require_open()
        if runtime.reconfiguring or runtime.session:
            raise ConfigurationLocked(
                "Das Tasterprogramm kann gerade nicht geändert werden."
            )
        ids = {program.id for program in runtime.configuration.temperature_programs}
        if (
            not isinstance(profile, str)
            or profile not in {"constant", *ids}
        ):
            raise ValueError("Ungültiges Tasterprogramm")
        configuration = replace(
            runtime.configuration,
            button_program=profile,
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
    async with runtime.serialized():
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


async def async_set_temperature_steps(hass, entry, values, *, actor=None):
    """Select explicitly entered stages through the same controller path."""
    runtime = entry.runtime_data
    async with runtime.serialized():
        runtime._require_open()
        if runtime.reconfiguring:
            raise ConfigurationLocked(
                "Die Grundeinstellungen werden gerade übernommen. Bitte kurz warten."
            )
        maximum = int(BY_KEY["temperature_gangs"].maximum)
        minimum = runtime.configuration.parameters.minimum_for("target_temperature_c")
        steps = validate_temperature_steps(
            values, "temperature_steps", minimum_c=minimum,
            maximum_c=BY_KEY["target_temperature_c"].maximum,
        )
        if len(steps) > maximum:
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
            actor=actor,
        )
        hass.config_entries.async_update_entry(
            entry, options=runtime.configuration.as_options()
        )
        return parameters.as_dict()


async def async_set_control_mode(hass, entry, mode, *, actor=None):
    """Switch controller mode and save the matching configuration atomically."""
    runtime = entry.runtime_data
    received_at = runtime._clock()
    async with runtime.serialized():
        runtime._require_open()
        if runtime.reconfiguring:
            raise ConfigurationLocked(
                "Die Grundeinstellungen werden gerade übernommen. Bitte kurz warten."
            )
        if not isinstance(mode, str) or mode not in {"automatic", "manual"}:
            raise ValueError("Ungültiger Betriebsmodus")
        mode_before = runtime.configuration.control_mode
        configuration = runtime._set_control_mode(mode, received_at=received_at)
        hass.config_entries.async_update_entry(
            entry, options=configuration.as_options()
        )
        await runtime._cycle()
        if mode_before != runtime.configuration.control_mode:
            runtime._record_notification_participant(actor)
        return configuration


async def async_set_entity_parameter(hass, entry, key, value, *, actor=None):
    try:
        await async_set_parameters(hass, entry, {key: value}, partial=True, actor=actor)
    except ParameterError as error:
        raise ValueError(parameter_error(error)) from None
