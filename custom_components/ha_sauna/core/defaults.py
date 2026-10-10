"""Load the single editable factory catalog without depending on Home Assistant.

Saved options take precedence in their consumers. The legacy section describes
historical formats and must not be changed when current factory values change.
Invalid catalogs fail at startup; there is deliberately no second set of defaults.
"""

import json
import re
from copy import deepcopy
from math import isfinite
from pathlib import Path

DEFAULTS_PATH = Path(__file__).resolve().parent.parent / "defaults.json"

# Consumer field identities are schema, not a second source of factory values.
_PARAMETER_KEYS = frozenset(
    [
        "session_gap_minutes",
        "confirmation_minutes",
        "heat_reset_minutes",
        "thermostat_cooldown_minutes",
        "minimum_heating_minutes",
        "after_run_minutes",
        "oven_cooling_max_minutes",
        "oven_cooling_half_life_minutes",
        "oven_cooling_heat_idle_ratio",
        "readiness_offset_c",
        "readiness_hysteresis_c",
        "warmup_estimation_minutes",
        "sauna_min_temperature_c",
        "preset_start_c",
        "preset_step_c",
        "preset_count",
        "target_temperature_c",
        "final_temperature_c",
        "temperature_gangs",
        "fault_confirmation_seconds",
        "sensor_timeout_seconds",
        "feedback_timeout_seconds",
        "power_heating_threshold_w",
        "manual_override_minutes",
        "nominal_power_kw",
        "button_hold_seconds",
        "light_reference_temperature_c",
        "light_transition_seconds",
        "light_brightness_scale",
        "night_brightness_percent",
        "operation_brightness_percent",
        "after_run_brightness_percent",
        "cooling_brightness_percent",
        "session_light_brightness_percent",
        "door_open_drop_c",
        "grid_seconds",
        "median_seconds",
        "door_window_seconds",
        "door_humidity_seconds",
        "door_open_slope",
        "door_open_humidity_upper",
        "door_open_humidity_lower",
        "door_open_hold_seconds",
        "door_heating_slope",
        "door_heating_hold_seconds",
        "door_close_slope",
        "door_close_hold_seconds",
        "vent_baseline_seconds",
        "vent_hold_seconds",
        "vent_drop_upper",
        "vent_drop_lower",
        "vent_absolute_humidity_loss_percent",
        "person_step_seconds",
        "strong_window_seconds",
        "strong_humidity_upper",
        "strong_humidity_lower",
        "strong_hold_seconds",
        "weak_window_seconds",
        "weak_humidity_upper",
        "weak_humidity_lower",
        "weak_hold_seconds",
        "infusion_window_seconds",
        "infusion_humidity",
        "infusion_hold_seconds",
        "light_output_hysteresis_percent",
    ]
)
_LEGACY_PARAMETER_KEYS = frozenset(
    [
        "temperature_increase_c",
        "program_1_start_c",
        "program_1_end_c",
        "program_1_gangs",
        "program_2_start_c",
        "program_2_end_c",
        "program_2_gangs",
        "door_heating_max_temperature_c",
        "strong_temperature_upper",
        "strong_temperature_lower",
        "weak_temperature_upper",
        "weak_temperature_lower",
        "infusion_temperature",
    ]
)
_COLOR_ROLES = frozenset(
    [
        "page_background",
        "card_background",
        "text",
        "muted_text",
        "border",
        "focus",
        "ui_accent",
        "ui_command",
        "ui_danger",
        "ui_feedback_on",
        "ui_feedback_off",
        "ui_heater_on",
        "ui_heater_off",
        "ui_heater_unknown",
        "status_info",
        "status_success",
        "status_warning",
        "status_error",
        "status_unknown",
        "phase_warmup",
        "phase_ready",
        "phase_session",
        "phase_cooling",
        "phase_forced_cooling",
        "phase_idle",
        "activity_ventilation",
        "series_upper",
        "series_lower",
        "series_temperature",
        "series_humidity",
        "series_light",
        "series_overview",
        "event_door",
        "event_infusion",
        "chart_background",
        "chart_text",
        "chart_grid",
        "chart_event",
        "chart_threshold",
        "chart_minimap_track",
        "detector_marker",
    ]
)


def _object(value, required, name, *, optional=()):
    if (
        not isinstance(value, dict)
        or not set(required) <= value.keys()
        or value.keys() - set(required) - set(optional)
    ):
        raise ValueError(f"defaults.json: invalid fields for {name}")
    return value


def _number(value, name):
    try:
        valid = (
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and isfinite(value)
        )
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"defaults.json: {name} must be a finite number")
    return value


def _integer(value, name, *, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"defaults.json: {name} must be an integer >= {minimum}")
    return value


def _groups(groups, name):
    if not isinstance(groups, list):
        raise ValueError(f"defaults.json: invalid {name}")  # noqa: TRY004 - uniform invalid-catalog ValueError contract.
    identities = set()
    for group in groups:
        _object(
            group, {"id", "label"}, name,
            optional=("description", "descriptions", "presence_sources")
            if name == "settings_subgroups" else ("surface",),
        )
        if any(
            not isinstance(group[key], str) or not group[key].strip()
            for key in ("id", "label", "description") if key in group
        ):
            raise ValueError(f"defaults.json: invalid {name}")
        if "surface" in group and group["surface"] not in ("panel", "integration"):
            raise ValueError(f"defaults.json: invalid {name} surface")
        sources = group.get("presence_sources")
        if sources is not None and (
            not isinstance(sources, list)
            or not sources
            or any(source not in ("proxy", "ha_presence") for source in sources)
            or len(set(sources)) != len(sources)
        ):
            raise ValueError(f"defaults.json: invalid {name} presence_sources")
        if "descriptions" in group:
            descriptions = group["descriptions"]
            _object(descriptions, {"proxy", "ha_presence"}, f"{name} descriptions")
            if any(not isinstance(value, str) or not value.strip()
                   for value in descriptions.values()):
                raise ValueError(f"defaults.json: invalid {name} descriptions")
        if group["id"] in identities:
            raise ValueError(f"defaults.json: duplicate {name}")
        identities.add(group["id"])
    return identities


def validate_catalog(catalog):
    """Validate editable data before any consumer can observe a partial catalog."""
    required = {
        "schema_version",
        "parameters",
        "legacy_parameters",
        "programs",
        "instance",
        "setup",
        "appearance",
        "frontend",
        "runtime",
    }
    if not isinstance(catalog, dict) or set(catalog) != required:
        raise ValueError("defaults.json: invalid sections")
    if _integer(catalog["schema_version"], "schema_version") != 1:
        raise ValueError("defaults.json: unsupported schema version")
    _object(catalog["runtime"], {"archive_projection_cache_entries"}, "runtime")
    _object(
        catalog["frontend"],
        {
            "history_cache_records",
            "temperature_step_c",
            "climate_temperature_step_c",
            "brightness_step_percent",
            "settings_groups",
            "temperature_dial_step_c",
            "warmup_minutes_step",
            "settings_subgroups",
            "control_button_height_px",
            "primary_action_height_px",
            "control_radius_px",
            "surface_radius_px",
            "surface_gap_px",
            "surface_padding_px",
            "control_group_gap_px",
        },
        "frontend",
    )
    definitions = {}
    fields = {
        "key",
        "label",
        "unit",
        "allow_zero",
        "optional",
        "maximum",
        "default",
        "minimum",
        "integer",
        "description",
        "group",
        "expert",
        "step",
        "number_step",
        "settings_group",
        "settings_subgroup",
        "order",
    }
    groups = catalog["frontend"]["settings_groups"]
    group_ids = _groups(groups, "settings_groups")
    if (
        not {
            "programs",
            "operation",
            "sensors",
            "light",
            "appearance",
            "maintenance",
            "personal",
        }
        <= group_ids
    ):
        raise ValueError("defaults.json: missing settings group")
    subgroups = catalog["frontend"]["settings_subgroups"]
    subgroup_ids = _groups(subgroups, "settings_subgroups")
    for section_name in ("parameters", "legacy_parameters"):
        if not isinstance(catalog[section_name], list):
            raise ValueError(f"defaults.json: {section_name} must be a list")  # noqa: TRY004 - uniform invalid-catalog ValueError contract.
        section_keys = set()
        for spec in catalog[section_name]:
            if not isinstance(spec, dict) or set(spec) != fields:
                raise ValueError("defaults.json: invalid parameter metadata")
            key = spec["key"]
            if not isinstance(key, str) or not key or key in definitions:
                raise ValueError("defaults.json: duplicate or invalid parameter key")
            section_keys.add(key)
            for name in ("label", "unit", "description", "group"):
                if not isinstance(spec[name], str) or not spec[name]:
                    raise ValueError(f"defaults.json: invalid {key}.{name}")
            for name in ("allow_zero", "optional", "integer", "expert"):
                if not isinstance(spec[name], bool):
                    raise ValueError(f"defaults.json: invalid {key}.{name}")  # noqa: TRY004 - uniform invalid-catalog ValueError contract.
            if spec["optional"]:
                raise ValueError(
                    f"defaults.json: invalid optional consumer value {key}"
                )
            if spec["settings_group"] not in group_ids:
                raise ValueError(f"defaults.json: unknown settings group for {key}")
            if spec["settings_subgroup"] not in {None, *subgroup_ids}:
                raise ValueError(f"defaults.json: unknown settings subgroup for {key}")
            _integer(spec["order"], f"{key}.order")
            minimum = spec["minimum"]
            if minimum is not None:
                _number(minimum, f"{key}.minimum")
            maximum = _number(spec["maximum"], f"{key}.maximum")
            if maximum < (minimum if minimum is not None else 0):
                raise ValueError(f"defaults.json: invalid bounds for {key}")
            for name in ("step", "number_step"):
                step = spec[name]
                if name == "step" and step == "any":
                    continue
                if _number(step, f"{key}.{name}") <= 0:
                    raise ValueError(f"defaults.json: invalid input step for {key}")
            value = spec["default"]
            if value is None:
                raise ValueError(f"defaults.json: missing default for {key}")
            else:
                _number(value, key)
                if spec["unit"] == "min":
                    _number(value * 60, f"{key}.seconds")
                if (
                    value > maximum
                    or (minimum is not None and value < minimum)
                    or (
                        minimum is None
                        and (value < 0 or (value == 0 and not spec["allow_zero"]))
                    )
                    or (spec["integer"] and not float(value).is_integer())
                ):
                    raise ValueError(f"defaults.json: invalid default for {key}")
            definitions[key] = spec
        expected_keys = (
            _PARAMETER_KEYS if section_name == "parameters" else _LEGACY_PARAMETER_KEYS
        )
        if section_keys != expected_keys:
            raise ValueError(f"defaults.json: invalid consumer keys in {section_name}")
    values = {key: spec["default"] for key, spec in definitions.items()}
    for key in ("preset_start_c", "target_temperature_c", "final_temperature_c"):
        if values[key] < values["sauna_min_temperature_c"]:
            raise ValueError(f"defaults.json: {key} below sauna minimum")
    if values["oven_cooling_max_minutes"] < values["after_run_minutes"]:
        raise ValueError("defaults.json: invalid cooling limits")
    for route in ("strong", "weak"):
        if values[f"{route}_window_seconds"] % values["person_step_seconds"]:
            raise ValueError("defaults.json: incompatible detection windows")
    instance = catalog["instance"]
    choices = {
        "log_level": {"ERROR", "INFO", "DEBUG"},
        "control_input_mode": {"switch", "button"},
        "program_mode": {"constant", "progressive"},
        "control_mode": {"automatic", "manual"},
        "presence_source": {"proxy", "ha_presence"},
        "button_session_gesture": {"long", "double", "triple"},
    }
    if not isinstance(instance, dict) or set(instance) != {
        *choices,
        "button_event_type",
        "button_program",
        "button_temperature_c",
    }:
        raise ValueError("defaults.json: invalid instance defaults")
    setup = catalog["setup"]
    if not isinstance(setup, dict) or set(setup) - {
        "control_input_mode",
        "program_mode",
    }:
        raise ValueError("defaults.json: invalid setup defaults")
    for key, allowed in choices.items():
        if instance[key] not in allowed or setup.get(key, instance[key]) not in allowed:
            raise ValueError(f"defaults.json: invalid choice for {key}")
    program_ids = set()
    if not isinstance(catalog["programs"], list):
        raise ValueError("defaults.json: programs must be a list")  # noqa: TRY004 - uniform invalid-catalog ValueError contract.
    for program in catalog["programs"]:
        if not isinstance(program, dict) or set(program) != {
            "id",
            "name",
            "start_c",
            "end_c",
            "distribution_gangs",
        }:
            raise ValueError("defaults.json: invalid program")
        identity = program["id"]
        if (
            not isinstance(identity, str)
            or not identity
            or identity in program_ids | {"constant", "progressive", "current"}
            or not isinstance(program["name"], str)
            or not program["name"].strip()
        ):
            raise ValueError("defaults.json: invalid program identity")
        program_ids.add(identity)
        for key in ("start_c", "end_c"):
            if (
                not values["sauna_min_temperature_c"]
                <= _number(program[key], key)
                <= definitions["target_temperature_c"]["maximum"]
            ):
                raise ValueError("defaults.json: program outside temperature bounds")
        count = _integer(program["distribution_gangs"], "distribution_gangs", minimum=1)
        if count > definitions["temperature_gangs"]["maximum"]:
            raise ValueError("defaults.json: invalid program distribution")
    if instance["button_program"] not in {"constant", *program_ids}:
        raise ValueError("defaults.json: invalid button program")
    if not isinstance(instance["button_event_type"], str):
        raise ValueError("defaults.json: invalid button event type")  # noqa: TRY004 - uniform invalid-catalog ValueError contract.
    button_temperature = instance["button_temperature_c"]
    if isinstance(button_temperature, dict):
        if button_temperature != {"parameter": "target_temperature_c"}:
            raise ValueError("defaults.json: invalid button temperature")
    elif not (
        values["sauna_min_temperature_c"]
        <= _number(button_temperature, "button_temperature_c")
        <= definitions["target_temperature_c"]["maximum"]
    ):
        raise ValueError("defaults.json: button temperature outside temperature bounds")
    appearance = catalog["appearance"]
    _object(appearance, {"colors", "scales", "instruments", "precision"}, "appearance")
    _object(appearance["instruments"],
            {"default", "temperature", "humidity", "light"}, "instruments")
    for name, instrument in appearance["instruments"].items():
        _object(instrument, {"label", "default", "options"}, "instrument")
        options = ["round", "linear"] if name == "default" else ["inherit", "round", "linear"]
        if (
            not isinstance(instrument["label"], str)
            or not instrument["label"].strip()
            or instrument["options"] != options
            or instrument["default"] not in options
        ):
            raise ValueError("defaults.json: invalid instrument style")
    _object(appearance["precision"], {"absolute_humidity", "energy"}, "precision")
    for value in appearance["precision"].values():
        _integer(value, "display precision")
    roles = set()
    if not isinstance(appearance["colors"], list):
        raise ValueError("defaults.json: colors must be a list")  # noqa: TRY004 - uniform invalid-catalog ValueError contract.
    for color in appearance["colors"]:
        _object(
            color,
            {"id", "label", "group", "default"},
            "color",
            optional={"hidden", "featured"},
        )
        if any(
            not isinstance(color[key], str) or not color[key].strip()
            for key in ("id", "label", "group")
        ):
            raise ValueError("defaults.json: invalid appearance role")
        if color["id"] in roles:
            raise ValueError("defaults.json: invalid appearance role")
        roles.add(color["id"])
        if any(
            not isinstance(color[key], bool)
            for key in ("hidden", "featured")
            if key in color
        ):
            raise ValueError("defaults.json: invalid appearance role flag")
        if (
            not isinstance(color["default"], str)
            or re.fullmatch(r"#[0-9a-fA-F]{6}", color["default"]) is None
        ):
            raise ValueError("defaults.json: invalid appearance color")
    if roles != _COLOR_ROLES:
        raise ValueError("defaults.json: missing or unknown appearance role")
    _object(appearance["scales"], {"temperature", "humidity"}, "scales")
    for name, scale in appearance["scales"].items():
        required_bounds = {"minimum", "maximum"} if name == "humidity" else set()
        _object(
            scale, {"default", *required_bounds}, name, optional={"minimum", "maximum"}
        )
        bounds = scale["default"]
        _object(bounds, {"minimum", "maximum"}, f"{name}.default")
        minimum = _number(bounds["minimum"], f"{name}.minimum")
        maximum = _number(bounds["maximum"], f"{name}.maximum")
        lower = _number(scale.get("minimum", minimum), f"{name}.lower_limit")
        upper = _number(scale.get("maximum", maximum), f"{name}.upper_limit")
        if (
            minimum >= maximum
            or not isfinite(maximum - minimum)
            or minimum < lower
            or maximum > upper
        ):
            raise ValueError("defaults.json: invalid appearance scale")
    for section_name in ("frontend", "runtime"):
        for key, value in catalog[section_name].items():
            if key in {"settings_groups", "settings_subgroups"}:
                continue
            if _number(value, key) <= 0:
                raise ValueError(f"defaults.json: invalid {key}")
            if key in {"history_cache_records", "archive_projection_cache_entries"}:
                _integer(value, key, minimum=1)
    return catalog


def load_catalog(path=DEFAULTS_PATH):
    """Read and validate a complete catalog, also usable by offline checks."""
    try:
        return validate_catalog(json.loads(Path(path).read_text(encoding="utf-8")))
    except (KeyError, TypeError, OverflowError) as error:
        raise ValueError("defaults.json: incomplete or invalid catalog") from error


_CATALOG = load_catalog()


def section(name):
    """Return detached data; consumers cannot mutate the shared catalog."""
    return deepcopy(_CATALOG[name])


def instance_default(key, *, setup=False, parameters=None):
    """Resolve a setup override, scalar value or explicit parameter reference."""
    values = (
        _CATALOG["setup"]
        if setup and key in _CATALOG["setup"]
        else _CATALOG["instance"]
    )
    value = values[key]
    if isinstance(value, dict) and "parameter" in value:
        parameter = value["parameter"]
        if parameters is not None and parameter in parameters:
            return deepcopy(parameters[parameter])
        return next(
            deepcopy(spec["default"])
            for spec in _CATALOG["parameters"]
            if spec["key"] == parameter
        )
    return deepcopy(value)
