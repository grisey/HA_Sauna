"""Einrichtung und einheitliche Konfiguration der Sauna-Integration."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector

from .bindings import BindingError, Bindings, ROLES, validate_metadata
from .const import CONF_BINDINGS, CONF_PARAMETERS, DOMAIN
from .core.parameters import BY_KEY, EDITABLE_DEFINITIONS, ParameterError, Parameters, LIVE_TEMPERATURE_KEYS
from .settings import async_set_parameters, ConfigurationLocked
from .log import LEVELS


def binding_schema(*, include_name: bool = False) -> vol.Schema:
    fields: dict = {}
    if include_name:
        fields[vol.Required("name")] = selector.TextSelector()
    for role in ROLES:
        entity_filter: dict[str, Any] = {"domain": list(role.domains)}
        if role.device_class:
            entity_filter["device_class"] = role.device_class
        marker = vol.Optional if role.optional else vol.Required
        fields[marker(role.key)] = selector.EntitySelector({"filter": entity_filter})
    fields[vol.Required("control_input_mode", default="button")] = selector.SelectSelector({
        "options": ["button", "switch"], "translation_key": "control_input_mode"})
    fields[vol.Optional("button_event_type", default="")] = selector.TextSelector()
    return vol.Schema(fields)


def parameter_schema(*, live_only=False, include_program_choices=False) -> vol.Schema:
    fields = {
        (vol.Optional if definition.optional else vol.Required)(
            definition.key, default=definition.default if definition.default is not None else vol.UNDEFINED,
        ): selector.NumberSelector({
            "min": definition.minimum if definition.minimum is not None else 0,
            "max": definition.maximum,
            "step": 1 if definition.integer else "any",
            "mode": selector.NumberSelectorMode.BOX,
            "unit_of_measurement": definition.unit,
        })
        for definition in EDITABLE_DEFINITIONS
        if not live_only or definition.key in LIVE_TEMPERATURE_KEYS
    }
    if include_program_choices:
        fields[vol.Required("program_mode", default="progressive")] = selector.SelectSelector({
            "options": ["constant", "progressive"], "translation_key": "program_mode"})
        fields[vol.Required("button_program", default="current")] = selector.SelectSelector({
            "options": ["current", "constant", "program_1", "program_2"],
            "translation_key": "button_program"})
    return vol.Schema(fields)


def checked_bindings(hass: HomeAssistant, user_input: dict[str, Any]) -> Bindings:
    bindings = Bindings({k: v for k, v in user_input.items() if k not in ("control_input_mode", "button_event_type")})
    metadata = {
        entity_id: state.attributes if (state := hass.states.get(entity_id)) else None
        for entity_id in bindings.values.values()
    }
    validate_metadata(bindings, metadata)
    return bindings


def heater_is_used(entries, bindings: Bindings, own_entry_id: str | None = None) -> bool:
    return any(
        entry.entry_id != own_entry_id
        and entry.options.get(CONF_BINDINGS, {}).get("heater") == bindings.values["heater"]
        for entry in entries
    )


class SaunaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Bindungen einmal speichern; alle veränderlichen Werte liegen in options."""

    VERSION = 1

    def __init__(self) -> None:
        self._name = ""
        self._bindings: Bindings | None = None
        self._input_options = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors = {}
        if user_input is not None:
            name = user_input.get("name", "")
            self._name = name.strip() if isinstance(name, str) else ""
            try:
                if not self._name:
                    raise BindingError("name", "required")
                bindings = checked_bindings(
                    self.hass, {key: value for key, value in user_input.items() if key != "name"},
                )
                if heater_is_used(self._async_current_entries(), bindings):
                    raise BindingError("heater", "heater_already_used")
                self._bindings = bindings
                self._input_options = {"control_input_mode": user_input.get("control_input_mode", "button"),
                    "button_event_type": user_input.get("button_event_type", "").strip()}
            except BindingError as error:
                errors[error.key] = error.code
            else:
                return await self.async_step_parameters()
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(binding_schema(include_name=True), user_input),
            errors=errors,
        )

    async def async_step_parameters(self, user_input: dict[str, Any] | None = None):
        if self._bindings is None:
            return self.async_abort(reason="missing_bindings")
        errors = {}
        if user_input is not None:
            try:
                values = dict(user_input)
                program_mode = values.pop("program_mode", "progressive")
                button_program = values.pop("button_program", "current")
                if program_mode not in ("constant", "progressive"):
                    raise ParameterError("program_mode", "invalid_program_mode")
                if button_program not in ("current", "constant", "program_1", "program_2"):
                    raise ParameterError("button_program", "invalid_button_program")
                parameters = Parameters(values)
                # Gegen parallel angelegte Einträge auch beim endgültigen Speichern prüfen.
                if heater_is_used(self._async_current_entries(), self._bindings):
                    return self.async_abort(reason="heater_already_used")
            except ParameterError as error:
                errors[error.key] = error.code
            else:
                return self.async_create_entry(
                    title=self._name,
                    data={},
                    options={
                        **self._input_options,
                        CONF_BINDINGS: self._bindings.as_dict(),
                        CONF_PARAMETERS: parameters.as_dict(),
                        "program_mode": program_mode,
                        "button_program": button_program,
                    },
                )
        return self.async_show_form(
            step_id="parameters",
            data_schema=self.add_suggested_values_to_schema(
                parameter_schema(include_program_choices=True), user_input),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry):
        return SaunaOptionsFlow()


class SaunaOptionsFlow(OptionsFlow):
    """Geänderte Zuordnungen und Parameter über dieselbe Eingabeprüfung speichern."""

    def _has_session(self) -> bool:
        runtime = getattr(self.config_entry, "runtime_data", None)
        if runtime is None or runtime.closed:
            return False
        try:
            runtime.check_configuration_change()
        except ValueError:
            return True
        return False

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(step_id="init", menu_options=["bindings", "parameters", "logging"])

    async def async_step_logging(self, user_input=None):
        errors = {}
        if user_input is not None:
            if user_input.get("log_level") in LEVELS:
                return self.async_create_entry(title="", data={
                    **self.config_entry.options, "log_level": user_input["log_level"],
                })
            errors["log_level"] = "invalid_log_level"
        return self.async_show_form(step_id="logging", data_schema=vol.Schema({
            vol.Required("log_level", default=self.config_entry.options.get("log_level", "INFO")):
                selector.SelectSelector({"options": list(LEVELS), "translation_key": "log_level"}),
        }), errors=errors)

    async def async_step_bindings(self, user_input: dict[str, Any] | None = None):
        if self._has_session():
            return self.async_abort(reason="session_exists")
        errors = {}
        if user_input is not None:
            try:
                bindings = checked_bindings(self.hass, user_input)
                if heater_is_used(
                    self.hass.config_entries.async_entries(DOMAIN), bindings, self.config_entry.entry_id,
                ):
                    raise BindingError("heater", "heater_already_used")
            except BindingError as error:
                errors[error.key] = error.code
            else:
                return self.async_create_entry(title="", data={
                    **self.config_entry.options, CONF_BINDINGS: bindings.as_dict(),
                    "control_input_mode": user_input.get("control_input_mode", self.config_entry.options.get("control_input_mode", "switch")),
                    "button_event_type": user_input.get("button_event_type", "").strip(),
                })
        return self.async_show_form(
            step_id="bindings",
            data_schema=self.add_suggested_values_to_schema(
                binding_schema(), user_input if user_input is not None else {
                    **self.config_entry.options[CONF_BINDINGS],
                    "control_input_mode": self.config_entry.options.get("control_input_mode", "switch"),
                    "button_event_type": self.config_entry.options.get("button_event_type", "")},
            ),
            errors=errors,
        )

    async def async_step_parameters(self, user_input: dict[str, Any] | None = None):
        live_only = self._has_session()
        runtime = getattr(self.config_entry, "runtime_data", None)
        errors = {}
        if user_input is not None:
            try:
                if runtime and not runtime.closed:
                    values = dict(user_input)
                    program_mode = values.pop("program_mode", self.config_entry.options.get("program_mode", "progressive"))
                    button_program = values.pop("button_program", self.config_entry.options.get("button_program", "current"))
                    if program_mode not in ("constant", "progressive"):
                        raise ParameterError("program_mode", "invalid_program_mode")
                    if button_program not in ("current", "constant", "program_1", "program_2"):
                        raise ParameterError("button_program", "invalid_button_program")
                    if live_only:
                        if set(values) - LIVE_TEMPERATURE_KEYS:
                            raise ConfigurationLocked()
                        target_changed = ("target_temperature_c" in values
                            and values["target_temperature_c"] != runtime.controller.target_temperature)
                        if not target_changed and program_mode == runtime.configuration.program_mode:
                            values.pop("target_temperature_c", None)
                    else:
                        target_changed = False
                    parameters = await async_set_parameters(self.hass, self.config_entry,
                        values, partial=live_only,
                        explicit_target=target_changed and program_mode == "constant",
                        program_mode=program_mode,
                        new_program=target_changed or program_mode != self.config_entry.options.get("program_mode", "progressive"))
                else:
                    values = dict(user_input)
                    program_mode = values.pop("program_mode", self.config_entry.options.get("program_mode", "progressive"))
                    button_program = values.pop("button_program", self.config_entry.options.get("button_program", "current"))
                    values.setdefault("temperature_increase_c", self.config_entry.options[
                        CONF_PARAMETERS].get("temperature_increase_c", BY_KEY["temperature_increase_c"].default))
                    parameters = Parameters(values).as_dict()
            except ParameterError as error:
                errors[error.key] = error.code
            except ConfigurationLocked:
                return self.async_abort(reason="session_exists")
            else:
                return self.async_create_entry(title="", data={
                    **self.config_entry.options, CONF_PARAMETERS: parameters,
                    "program_mode": program_mode,
                    "button_program": button_program,
                })
        suggested = dict(self.config_entry.options[CONF_PARAMETERS])
        suggested["program_mode"] = self.config_entry.options.get("program_mode", "progressive")
        suggested["button_program"] = self.config_entry.options.get("button_program", "current")
        if live_only:
            suggested["target_temperature_c"] = runtime.controller.target_temperature
        return self.async_show_form(
            step_id="parameters",
            data_schema=self.add_suggested_values_to_schema(
                parameter_schema(live_only=live_only, include_program_choices=True),
                user_input if user_input is not None else suggested,
            ),
            errors=errors,
        )
