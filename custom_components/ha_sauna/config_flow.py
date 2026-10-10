"""Gerätezuordnung und dauerhafte Anlagenwerte der Sauna-Integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section as form_section
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .bindings import ROLE_BY_KEY, ROLES, BindingError, Bindings, metadata_error, validate_metadata
from .const import CONF_BINDINGS, DOMAIN
from .core.defaults import instance_default, section
from .core.parameters import BY_KEY, EDITABLE_DEFINITIONS, LIVE_TEMPERATURE_KEYS, ParameterError, Parameters
from .presentation import parameter_error
from .runtime import Configuration
from .settings import parameter_change


def internal_control_source(hass: HomeAssistant, key: str, entity_id: str) -> bool:
    """Logical sauna controls cannot serve as hardware actors or feedback."""
    if key not in {
        "heater", "light", "control_input", "presence", "heater_feedback",
        "heater_power", "audio_output",
    }:
        return False
    entry = er.async_get(hass).async_get(entity_id)
    return entry is not None and entry.platform == DOMAIN


DEVICE_ROLES = {
    "upper_device": ("upper_temperature", "upper_humidity", "upper_status"),
    "lower_device": ("lower_temperature", "lower_humidity", "lower_status"),
    "presence_device": ("presence", "presence_illuminance"),
}


BINDING_SECTIONS = {
    "upper_sensors": DEVICE_ROLES["upper_device"],
    "lower_sensors": DEVICE_ROLES["lower_device"],
    "presence_sensors": DEVICE_ROLES["presence_device"],
    "additional_devices": ("heater_feedback", "heater_power", "audio_output"),
    "environment": tuple(role.key for role in ROLES if role.key.startswith("environment_")),
}
BINDING_SECTION_BY_KEY = {
    key: group for group, keys in BINDING_SECTIONS.items() for key in keys
}


def pack_binding_input(values):
    """Pack flat configuration values into the native form section payload."""
    packed = {group: {} for group in BINDING_SECTIONS}
    for key, value in values.items():
        if group := BINDING_SECTION_BY_KEY.get(key):
            packed[group][key] = value
        else:
            packed[key] = value
    return packed


def unpack_binding_input(values):
    """Flatten only declared section fields; leave unexpected keys for validation."""
    unpacked = {}
    for key, value in values.items():
        if key in BINDING_SECTION_BY_KEY:
            raise BindingError("base", "unknown_binding")
        if key not in BINDING_SECTIONS:
            unpacked[key] = value
            continue
        if not isinstance(value, dict) or set(value) - set(BINDING_SECTIONS[key]):
            raise BindingError("base", "unknown_binding")
        unpacked.update(value)
    return unpacked


def binding_form_errors(errors):
    """Native sections display errors on their heading, keeping them visible."""
    return {BINDING_SECTION_BY_KEY.get(key, key): value for key, value in errors.items()}


def binding_devices(hass: HomeAssistant, saved) -> dict[str, str]:
    """Use the primary measurement as device suggestion; keep manual corrections."""
    registry = er.async_get(hass)
    result = {}
    for device_key, keys in DEVICE_ROLES.items():
        entity_id = saved.get(keys[0])
        entry = registry.async_get(entity_id) if entity_id else None
        if entry is not None and entry.device_id:
            result[device_key] = entry.device_id
    return result


def device_schema(hass: HomeAssistant, *, include_name=False, saved=None) -> vol.Schema:
    fields = {}
    if include_name:
        fields[vol.Required("name")] = selector.TextSelector()
    registry = er.async_get(hass)
    states = hass.states.async_all()
    previous_devices = binding_devices(hass, saved or {})
    for device_key, keys in DEVICE_ROLES.items():
        devices = set()
        # Generic status entities alone do not identify a measurement device.
        measurement_keys = tuple(key for key in keys if not key.endswith("_status"))
        for state in states:
            entry = registry.async_get(state.entity_id)
            if entry is None or not entry.device_id:
                continue
            if any(metadata_error(ROLE_BY_KEY[key], state.entity_id, state.attributes) is None
                   and not internal_control_source(hass, key, state.entity_id)
                   for key in measurement_keys):
                devices.add(entry.device_id)
        previous = previous_devices.get(device_key)
        if previous:
            devices.add(previous)
        device_registry = dr.async_get(hass) if devices else None
        options = []
        for device_id in sorted(devices):
            device = device_registry.async_get(device_id)
            label = (device.name_by_user or device.name or device_id) if device else device_id
            options.append({"value": device_id, "label": label})
        fields[vol.Optional(device_key)] = selector.SelectSelector({
            "options": options, "mode": "dropdown",
        })
    return vol.Schema(fields)


def device_bindings(hass: HomeAssistant, devices, *, saved=None):
    """Suggest only unique compatible entities; changed devices replace their roles."""
    saved = saved or {}
    values = dict(saved)
    errors = {}
    previous = binding_devices(hass, saved)
    registry = er.async_get(hass)
    states = hass.states.async_all()
    for device_key, keys in DEVICE_ROLES.items():
        device_id = devices.get(device_key)
        if not device_id or device_id == previous.get(device_key):
            continue
        for key in keys:
            values.pop(key, None)
            candidates = []
            for state in states:
                entry = registry.async_get(state.entity_id)
                if entry is not None and entry.device_id == device_id and (
                    metadata_error(ROLE_BY_KEY[key], state.entity_id, state.attributes) is None
                    and not internal_control_source(hass, key, state.entity_id)
                ):
                    candidates.append(state.entity_id)
            if len(candidates) == 1:
                values[key] = candidates[0]
            elif len(candidates) > 1:
                errors[key] = "ambiguous_entity"
    return values, errors


def binding_schema(hass: HomeAssistant, *, include_name: bool = False, saved=None, errors=None) -> vol.Schema:
    fields: dict = {}
    saved = saved or {}
    states = hass.states.async_all()
    if include_name:
        fields[vol.Required("name")] = selector.TextSelector()
    for role in ROLES:
        entity_filter: dict[str, Any] = {"domain": list(role.domains)}
        if role.device_class:
            entity_filter["device_class"] = role.device_class
        marker = vol.Optional if role.optional else vol.Required
        candidates = [state.entity_id for state in states
                      if metadata_error(role, state.entity_id, state.attributes) is None
                      and not internal_control_source(hass, role.key, state.entity_id)]
        previous = saved.get(role.key)
        previous_state = hass.states.get(previous) if previous else None
        if previous and previous_state is None and not internal_control_source(hass, role.key, previous):
            candidates.append(previous)
        fields[marker(role.key)] = selector.EntitySelector({
            "filter": entity_filter, "include_entities": sorted(set(candidates)),
        })
        if role.key == "control_input":
            fields[
                vol.Required(
                    "control_input_mode",
                    default=saved.get(
                        "control_input_mode", instance_default("control_input_mode", setup=include_name)
                    ),
                )
            ] = selector.SelectSelector(
                {"options": ["button", "switch"], "translation_key": "control_input_mode"}
            )
            fields[
                vol.Optional(
                    "button_event_type",
                    default=saved.get("button_event_type", instance_default("button_event_type"))
                )
            ] = selector.TextSelector()
        elif role.key == "presence":
            fields[
                vol.Required(
                    "presence_source",
                    default=saved.get("presence_source", instance_default("presence_source")),
                )
            ] = selector.SelectSelector(
                {"options": ["proxy", "ha_presence"], "translation_key": "presence_source"}
            )
    grouped = {}
    for marker, field in fields.items():
        key = str(marker)
        group = BINDING_SECTION_BY_KEY.get(key)
        if group is None:
            grouped[marker] = field
            continue
        group_marker = vol.Required(group)
        if group_marker in grouped:
            continue
        keys = BINDING_SECTIONS[group]
        complete = True
        if group in ("upper_sensors", "lower_sensors"):
            complete = all(saved.get(key) for key in keys[:2])
        elif group == "presence_sensors":
            complete = bool(saved.get("presence"))
        collapsed = complete and not any(key in (errors or {}) for key in keys)
        if group in ("upper_sensors", "lower_sensors") and (errors or {}).get("base") == "sensor_pair_required":
            collapsed = False
        grouped[group_marker] = form_section(
            vol.Schema({marker: value for marker, value in fields.items() if str(marker) in keys}),
            {"collapsed": collapsed},
        )
    return vol.Schema(grouped)


def checked_bindings(hass: HomeAssistant, user_input: dict[str, Any], *, saved=None) -> Bindings:
    if user_input.get("presence_source", instance_default("presence_source")) not in ("proxy", "ha_presence"):
        raise BindingError("presence_source", "invalid_presence_source")
    if user_input.get("presence_source") == "ha_presence" and not user_input.get("presence"):
        raise BindingError("presence", "entity_required")
    bindings = Bindings(
        {
            k: v
            for k, v in user_input.items()
            if k not in ("control_input_mode", "button_event_type", "presence_source")
        }
    )
    for key, entity_id in bindings.values.items():
        if internal_control_source(hass, key, entity_id):
            raise BindingError(key, "internal_control_source")
    metadata = {
        entity_id: state.attributes if (state := hass.states.get(entity_id)) else None
        for entity_id in bindings.values.values()
    }
    preserved = {
        key for key, entity_id in bindings.values.items()
        if (saved or {}).get(key) == entity_id
        and hass.states.get(entity_id) is None
    }
    validate_metadata(bindings, metadata, preserved=preserved)
    return bindings


def heater_is_used(
    entries, bindings: Bindings, own_entry_id: str | None = None
) -> bool:
    return any(
        entry.entry_id != own_entry_id
        and entry.options.get(CONF_BINDINGS, {}).get("heater")
        == bindings.values["heater"]
        for entry in entries
    )


class SaunaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Bindungen einmal speichern; alle veränderlichen Werte liegen in options."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors = {}
        if user_input is not None:
            name = user_input.get("name", "")
            if not isinstance(name, str) or not name.strip():
                errors["name"] = "required"
            else:
                self._device_values, self._device_errors = device_bindings(self.hass, user_input)
                self._device_values["name"] = name.strip()
                return await self.async_step_entities()
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                device_schema(self.hass, include_name=True), user_input
            ),
            errors=errors,
        )

    async def async_step_entities(self, user_input: dict[str, Any] | None = None):
        errors = {}
        if user_input is not None:
            try:
                user_input = unpack_binding_input(user_input)
            except BindingError as error:
                errors[error.key] = error.code
                user_input = None
        if user_input is not None:
            name = user_input.get("name", "")
            name = name.strip() if isinstance(name, str) else ""
            try:
                if not name:
                    raise BindingError("name", "required")
                bindings = checked_bindings(
                    self.hass,
                    {key: value for key, value in user_input.items() if key != "name"},
                )
                if heater_is_used(self._async_current_entries(), bindings):
                    raise BindingError("heater", "heater_already_used")
                configuration = Configuration(
                    bindings=bindings,
                    parameters=Parameters({}),
                    presence_source=user_input.get(
                        "presence_source", instance_default("presence_source")
                    ),
                    control_input_mode=user_input.get(
                        "control_input_mode", instance_default("control_input_mode", setup=True)
                    ),
                    button_event_type=user_input.get(
                        "button_event_type", instance_default("button_event_type")
                    ).strip(),
                    program_mode=instance_default("program_mode", setup=True),
                )
            except BindingError as error:
                errors[error.key] = error.code
            else:
                return self.async_create_entry(
                    title=name, data={}, options=configuration.as_options()
                )
        suggested = user_input if user_input is not None else getattr(self, "_device_values", {})
        errors = errors or (getattr(self, "_device_errors", {}) if user_input is None else {})
        return self.async_show_form(
            step_id="entities",
            data_schema=self.add_suggested_values_to_schema(
                binding_schema(self.hass, include_name=True, saved=suggested, errors=errors),
                pack_binding_input(suggested)
            ),
            errors=binding_form_errors(errors),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry):
        return SaunaOptionsFlow()


class SaunaOptionsFlow(OptionsFlow):
    """Anlagenwerte ändern und unabhängige Panel-Einstellungen erhalten."""

    def _has_session(self) -> bool:
        runtime = getattr(self.config_entry, "runtime_data", None)
        if runtime is None or runtime.closed:
            return False
        if runtime.reconfiguring:
            return True
        try:
            runtime.check_configuration_change()
        except ValueError:
            return True
        return False

    async def async_step_init(self, user_input=None):
        if self._has_session():
            return self.async_abort(reason="session_exists")
        return self.async_show_menu(
            step_id="init", menu_options=["bindings", *integration_groups()]
        )

    async def _async_step_area(self, area, user_input=None):
        if self._has_session():
            return self.async_abort(reason="session_exists")
        return self.async_show_menu(
            step_id=area,
            menu_options=[
                f"parameters_{group['id']}"
                for group in section("frontend")["settings_subgroups"]
                if self._definitions(group["id"], area=area)
            ],
        )

    def _definitions(self, subgroup, *, area=None):
        source = self.config_entry.options.get("presence_source", instance_default("presence_source"))
        group = next(
            item for item in section("frontend")["settings_subgroups"]
            if item["id"] == subgroup
        )
        if source not in group.get("presence_sources", ("proxy", "ha_presence")):
            return ()
        areas = integration_groups()
        return tuple(
            definition for definition in EDITABLE_DEFINITIONS
            if definition.settings_group in areas
            and (area is None or definition.settings_group == area)
            and definition.settings_subgroup == subgroup
            and definition.key not in LIVE_TEMPERATURE_KEYS
            and definition.minimum != definition.maximum
        )

    async def _async_step_parameters(self, subgroup, user_input=None):
        if self._has_session():
            return self.async_abort(reason="session_exists")
        definitions = self._definitions(subgroup)
        if not definitions:
            return self.async_abort(reason="settings_unavailable")
        current = Configuration.from_options(self.config_entry.options)
        effective = current.parameters.as_dict()
        keys = {definition.key for definition in definitions}
        if not hasattr(self, "_parameter_baselines"):
            self._parameter_baselines = {}
        if user_input is None:
            self._parameter_baselines[subgroup] = {
                key: effective.get(key) for key in keys
            }
        baseline = self._parameter_baselines.setdefault(
            subgroup, {key: effective.get(key) for key in keys}
        )
        errors = {}
        group = next(
            item for item in section("frontend")["settings_subgroups"]
            if item["id"] == subgroup
        )
        placeholders = {
            "description": group.get("descriptions", {}).get(
                current.presence_source, group.get("description", "")
            )
        }
        if user_input is not None:
            try:
                if set(user_input) - keys:
                    raise ParameterError("base", "unknown_parameter")
                for definition in definitions:
                    if not definition.optional and definition.key not in user_input:
                        raise ParameterError(definition.key, "required")
                    if definition.key in user_input:
                        definition.validate(user_input[definition.key])
                edits = {
                    key: value for key, value in user_input.items()
                    if value != baseline.get(key)
                }
                change = parameter_change(
                    current, edits, partial=True, explicit_target=False
                )
                candidate = {
                    **self.config_entry.options,
                    "parameters": {
                        **current.parameters.as_dict(),
                        **{key: change.configuration.parameters.values[key] for key in edits},
                    },
                }
                Configuration.from_options(candidate)
            except ParameterError as error:
                if error.key in keys:
                    errors[error.key] = error.code
                else:
                    errors["base"] = "parameter_dependency"
                    definition = BY_KEY.get(error.key)
                    placeholders.update({
                        "parameter": definition.label if definition else "Anlagenwerte",
                        "reason": parameter_error(error),
                    })
            except ValueError:
                errors["base"] = "invalid_configuration"
            else:
                return self.async_create_entry(title="", data=candidate)
        fields = {}
        for definition in definitions:
            marker = vol.Optional if definition.optional else vol.Required
            fields[marker(definition.key)] = selector.NumberSelector({
                "min": definition.minimum if definition.minimum is not None else 0,
                "max": definition.maximum,
                "step": definition.step,
                "unit_of_measurement": definition.unit,
                "mode": "box",
            })
        return self.async_show_form(
            step_id=f"parameters_{subgroup}",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(fields), user_input if user_input is not None else effective
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_bindings(self, user_input: dict[str, Any] | None = None):
        if self._has_session():
            return self.async_abort(reason="session_exists")
        saved = self.config_entry.options[CONF_BINDINGS]
        if user_input is not None:
            self._device_values, self._device_errors = device_bindings(
                self.hass, user_input, saved=saved
            )
            return await self.async_step_binding_entities()
        return self.async_show_form(
            step_id="bindings",
            data_schema=self.add_suggested_values_to_schema(
                device_schema(self.hass, saved=saved), binding_devices(self.hass, saved)
            ),
        )

    async def async_step_binding_entities(self, user_input: dict[str, Any] | None = None):
        if self._has_session():
            return self.async_abort(reason="session_exists")
        errors = {}
        if user_input is not None:
            try:
                user_input = unpack_binding_input(user_input)
            except BindingError as error:
                errors[error.key] = error.code
                user_input = None
        saved = {
            **self.config_entry.options[CONF_BINDINGS],
            **{
                key: self.config_entry.options.get(key, instance_default(key))
                for key in ("presence_source", "control_input_mode", "button_event_type")
            },
        }
        suggested = (
            {
                **{key: saved[key] for key in (
                    "presence_source", "control_input_mode", "button_event_type"
                )},
                **user_input,
            }
            if user_input is not None else {**saved, **getattr(self, "_device_values", saved)}
        )
        if user_input is None and hasattr(self, "_device_values"):
            # Removed roles from a changed device must not return via saved suggestions.
            for key in ROLE_BY_KEY:
                if key not in self._device_values:
                    suggested.pop(key, None)
            errors = errors or self._device_errors
        if user_input is not None:
            try:
                input_options = {
                    key: user_input.get(
                        key, self.config_entry.options.get(key, instance_default(key))
                    )
                    for key in ("presence_source", "control_input_mode", "button_event_type")
                }
                input_options["button_event_type"] = input_options["button_event_type"].strip()
                bindings = checked_bindings(
                    self.hass, {**user_input, **input_options},
                    saved=self.config_entry.options[CONF_BINDINGS],
                )
                if heater_is_used(
                    self.hass.config_entries.async_entries(DOMAIN),
                    bindings,
                    self.config_entry.entry_id,
                ):
                    raise BindingError("heater", "heater_already_used")
                candidate = {
                    **self.config_entry.options,
                    CONF_BINDINGS: bindings.as_dict(),
                    **input_options,
                }
                # Validate compatibility, but preserve the original stored values
                # instead of persisting normalization of unrelated panel settings.
                try:
                    Configuration.from_options(candidate)
                except ValueError as error:
                    if (
                        candidate.get("button_session_gesture", instance_default("button_session_gesture")) != "long"
                        and not bindings.values["control_input"].startswith("event.")
                    ):
                        raise BindingError("control_input", "button_gesture_incompatible") from error
                    raise BindingError("base", "invalid_configuration") from error
            except BindingError as error:
                errors[error.key] = error.code
            else:
                return self.async_create_entry(
                    title="",
                    data=candidate,
                )
        return self.async_show_form(
            step_id="binding_entities",
            data_schema=self.add_suggested_values_to_schema(
                binding_schema(self.hass, saved=suggested, errors=errors),
                pack_binding_input(suggested),
            ),
            errors=binding_form_errors(errors),
        )


def integration_groups():
    """The catalog owns which settings belong to installation configuration."""
    return tuple(
        group["id"] for group in section("frontend")["settings_groups"]
        if group.get("surface") == "integration"
    )


def _area_step(area):
    async def step(self, user_input=None):
        return await self._async_step_area(area, user_input)
    return step


def _parameter_step(subgroup):
    async def step(self, user_input=None):
        return await self._async_step_parameters(subgroup, user_input)
    return step


# Home Assistant dispatches named steps; the catalog supplies their identities.
for _area in integration_groups():
    setattr(SaunaOptionsFlow, f"async_step_{_area}", _area_step(_area))
for _subgroup in section("frontend")["settings_subgroups"]:
    setattr(
        SaunaOptionsFlow, f"async_step_parameters_{_subgroup['id']}",
        _parameter_step(_subgroup["id"]),
    )
