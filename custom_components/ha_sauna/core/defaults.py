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
        "after_run_brightness_percent",
        "after_run_minutes",
        "archive_export_chunk_bytes",
        "archive_history_page_records",
        "archive_projection_cache_entries",
        "archive_read_page_records",
        "button_hold_seconds",
        "climate_temperature_step_c",
        "confirmation_minutes",
        "cooling_brightness_percent",
        "curve_huber_delta",
        "curve_max_candidates",
        "curve_max_points",
        "curve_min_side_samples",
        "curve_original_background_residual",
        "curve_original_background_slope",
        "curve_original_continuation_contrast",
        "curve_original_gap_humidity_lower_seconds",
        "curve_original_gap_humidity_upper_seconds",
        "curve_original_gap_temperature_lower_seconds",
        "curve_original_gap_temperature_upper_seconds",
        "curve_original_new_infusion_contrast",
        "curve_original_noise_humidity_lower",
        "curve_original_noise_humidity_upper",
        "curve_original_noise_temperature_lower",
        "curve_original_noise_temperature_upper",
        "curve_original_open_contrast",
        "curve_original_samples",
        "curve_original_significance",
        "curve_robust_iterations",
        "curve_window_seconds",
        "door_humidity_seconds",
        "door_window_seconds",
        "electricity_price_eur_per_kwh",
        "fault_confirmation_seconds",
        "feedback_timeout_seconds",
        "final_temperature_c",
        "grid_seconds",
        "heat_reset_minutes",
        "history_context_seconds",
        "humidity_change_absolute_tolerance",
        "infusion_window_seconds",
        "light_brightness_scale",
        "light_cooling_fade_share",
        "light_day_elevation_degrees",
        "light_night_elevation_degrees",
        "light_output_hysteresis_percent",
        "light_reference_temperature_c",
        "light_transition_seconds",
        "manual_override_minutes",
        "median_seconds",
        "minimum_heating_minutes",
        "night_brightness_percent",
        "nominal_power_kw",
        "operation_brightness_percent",
        "oven_cooling_half_life_minutes",
        "oven_cooling_heat_idle_ratio",
        "oven_cooling_max_minutes",
        "person_step_seconds",
        "phase_completion_tolerance_seconds",
        "power_heating_threshold_w",
        "presence_fallback_delay_seconds",
        "presence_recovery_delay_seconds",
        "preset_count",
        "preset_start_c",
        "preset_step_c",
        "readiness_hysteresis_c",
        "readiness_offset_c",
        "runtime_tick_seconds",
        "sauna_min_temperature_c",
        "sensor_timeout_seconds",
        "session_gap_minutes",
        "session_light_brightness_percent",
        "standard_temperature_c",
        "strong_hold_seconds",
        "strong_humidity_lower",
        "strong_humidity_upper",
        "strong_window_seconds",
        "target_temperature_c",
        "temperature_gangs",
        "thermostat_cooldown_minutes",
        "vent_absolute_humidity_loss_percent",
        "vent_baseline_seconds",
        "vent_drop_lower",
        "vent_drop_upper",
        "vent_hold_seconds",
        "warmup_drop_rise_ratio",
        "warmup_estimation_minutes",
        "warmup_fit_grace_window_ratio",
        "warmup_history_blend_window_ratio",
        "warmup_minimum_points",
        "warmup_minimum_span_ratio",
        "warmup_noise_rise_ratio",
        "warmup_rate_relative_tolerance",
        "warmup_slow_confirmation_window_ratio",
        "warmup_smoothing_window_ratio",
        "weak_hold_seconds",
        "weak_humidity_lower",
        "weak_humidity_upper",
        "weak_window_seconds",
    ]
)


_OPTIONAL_PARAMETER_KEYS = frozenset({"electricity_price_eur_per_kwh"})


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
        "door_open_drop_c",
        "door_open_slope",
        "door_open_humidity_upper",
        "door_open_humidity_lower",
        "door_open_hold_seconds",
        "door_heating_slope",
        "door_heating_hold_seconds",
        "door_close_slope",
        "door_close_hold_seconds",
        "infusion_humidity",
        "infusion_hold_seconds",
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


_PREFERENCE_KEYS = frozenset(
    [
        "status_active_poll_ms",
        "status_idle_poll_ms",
        "history_live_poll_ms",
        "history_cache_records",
        "detail_records_per_page",
        "detail_events_per_page",
        "typeahead_reset_ms",
        "program_saved_feedback_ms",
        "settings_reload_attempts",
        "settings_reload_interval_ms",
        "history_wheel_idle_ms",
        "history_wheel_zoom_coefficient",
        "history_max_zoom",
        "history_keyboard_pan_fraction",
        "history_keyboard_selection_fraction",
        "history_zoom_factor",
        "history_selection_context_fraction",
        "history_view_transition_ms",
        "history_minimum_span_ms",
        "program_drag_edge_px",
        "program_drag_scroll_px",
        "select_menu_gap_px",
        "info_popup_gap_px",
        "timestamp_formatter_cache_entries",
        "value_precision",
        "detail_value_precision",
        "percentage_precision",
        "energy_precision",
        "absolute_humidity_precision",
        "history_readout_precision",
        "history_temperature_padding_c",
        "history_temperature_scale_step_c",
        "history_humidity_padding_percent",
        "history_humidity_scale_step_percent",
        "history_humidity_minimum_maximum_percent",
        "history_axis_min_ticks",
        "history_axis_max_ticks",
        "history_value_tick_spacing_px",
        "history_time_tick_spacing_px",
        "history_hover_radius_px",
        "history_phase_snap_radius_px",
        "history_tap_movement_px",
        "event_marker_size_px",
        "history_event_dot_size_px",
        "history_compact_band_height_px",
        "event_marker_gap_px",
        "diagnostic_columns_breakpoint_px",
        "diagnostic_fallback_width_px",
        "diagnostic_min_plot_width_px",
        "diagnostic_plot_gap_px",
        "diagnostic_plot_padding_px",
        "history_plot_width_px",
        "history_plot_height_px",
        "history_plot_side_padding_px",
        "history_plot_top_padding_px",
        "history_plot_bottom_padding_px",
        "history_overview_height_px",
        "detail_plot_width_px",
        "detail_plot_height_px",
        "diagnostic_plot_height_px",
        "chart_curve_width_px",
        "instrument_tick_target",
        "instrument_tick_maximum",
        "instrument_minor_ticks",
        "temperature_step_c",
        "temperature_dial_step_c",
        "brightness_step_percent",
        "warmup_minutes_step",
        "control_button_height_px",
        "primary_action_height_px",
        "control_radius_px",
        "surface_radius_px",
        "surface_gap_px",
        "surface_padding_px",
        "control_group_gap_px",
        "detail_plot_side_padding_px",
        "detail_plot_right_padding_px",
        "detail_plot_top_padding_px",
        "detail_plot_bottom_padding_px",
        "detail_axis_top_padding_px",
        "detail_axis_bottom_padding_px",
        "detail_value_label_top_padding_px",
        "detail_time_label_bottom_padding_px",
        "detail_curve_width_px",
        "diagnostic_scale_padding",
        "diagnostic_axis_ticks",
        "instrument_tick_significant_digits",
        "font_size_body_emphasis_px",
        "shadow_3_0_px",
        "shadow_5_0_px",
        "shadow_14_0_px",
        "shadow_30_0_px",
        "shadow_8_0_px",
        "shadow_2_0_px",
        "shadow_7_0_px",
        "shadow_4_0_px",
        "size_1280_0_px",
        "layout_space_33_px",
        "layout_space_36_px",
        "layout_space_22_px",
        "layout_space_09_px",
        "layout_space_24_px",
        "layout_space_20_px",
        "layout_space_16_px",
        "size_20_0_px",
        "font_size_phase_px",
        "tracking_0_6_px",
        "font_size_heading_emphasis_px",
        "layout_space_18_px",
        "font_size_body_px",
        "layout_space_13_px",
        "font_size_compact_px",
        "border_1_0_px",
        "layout_space_15_px",
        "layout_space_19_px",
        "border_2_0_px",
        "layout_space_08_px",
        "layout_space_37_px",
        "size_21_0_px",
        "size_16_0_px",
        "size_5_0_px",
        "layout_space_07_px",
        "transition_140_0_ms",
        "shadow_1_0_px",
        "border_4_0_px",
        "transition_350_0_ms",
        "font_size_subtitle_px",
        "font_size_caption_px",
        "size_130_0_px",
        "size_48_0_px",
        "size_96_0_px",
        "size_118_0_px",
        "size_40_0_px",
        "layout_space_12_px",
        "size_18_0_em",
        "size_110_0_px",
        "border_3_0_px",
        "breakpoint_540_0_px",
        "tracking_0_5_px",
        "radius_20_0_px",
        "layout_space_17_px",
        "radius_8_0_px",
        "size_76_0_px",
        "font_size_small_px",
        "size_108_0_px",
        "breakpoint_500_0_px",
        "size_22_0_px",
        "size_12_0_px",
        "size_10_0_px",
        "size_180_0_px",
        "layout_space_38_px",
        "breakpoint_800_0_px",
        "font_size_display_small_px",
        "breakpoint_450_0_px",
        "font_size_display_px",
        "font_size_heading_px",
        "size_32_0_px",
        "size_280_0_px",
        "size_400_0_px",
        "size_220_0_px",
        "size_5_0_rem",
        "size_14_0_rem",
        "size_3_0_px",
        "font_size_large_px",
        "size_64_0_px",
        "size_300_0_px",
        "size_54_0_px",
        "size_240_0_px",
        "size_340_0_px",
        "layout_space_28_px",
        "size_210_0_px",
        "breakpoint_700_0_px",
        "size_18_0_px",
        "size_8_0_px",
        "size_6_0_rem",
        "size_120_0_px",
        "font_size_fine_px",
        "size_13_0_px",
        "breakpoint_420_0_px",
        "breakpoint_240_0_px",
        "size_150_0_px",
        "shadow_16_0_px",
        "size_24_0_px",
        "layout_space_05_px",
        "stroke_3_0_px",
        "layout_space_21_px",
        "breakpoint_1000_0_px",
        "size_36_0_rem",
        "size_5_5_rem",
        "layout_space_10_px",
        "size_105_0_px",
        "size_25_0_px",
        "size_320_0_px",
        "size_270_0_px",
        "tracking_0_025_em",
        "radius_3_0_px",
        "size_2_0_px",
        "radius_2_0_px",
        "size_4_0_em",
        "size_7_5_em",
        "size_20_0_em",
        "size_12_0_em",
        "breakpoint_280_0_px",
        "size_1_0_px",
        "transition_1_2_s",
        "size_17_0_px",
        "size_4_0_px",
        "size_8_0_em",
        "breakpoint_520_0_px",
        "size_94_0_px",
        "size_7_0_em",
        "layout_space_32_px",
        "tracking_0_035_em",
        "font_size_instrument_px",
        "tracking_0_8_px",
        "font_size_micro_px",
        "tracking_1_5_px",
        "tracking_0_45_px",
        "size_100_0_px",
        "size_28_0_px",
        "breakpoint_220_0_px",
        "breakpoint_110_0_px",
        "radius_5_0_px",
        "tracking_0_08_em",
        "size_60_0_px",
        "size_70_0_px",
        "size_88_0_px",
        "font_size_display_emphasis_px",
        "stroke_1_7_ratio",
        "font_weight_600_0_ratio",
        "opacity_0_5_ratio",
        "line_height_1_3_ratio",
        "font_weight_700_0_ratio",
        "font_weight_650_0_ratio",
        "font_weight_500_0_ratio",
        "stroke_1_0_ratio",
        "stroke_2_0_ratio",
        "stroke_22_0_ratio",
        "stroke_4_0_ratio",
        "opacity_0_35_ratio",
        "stroke_6_0_ratio",
        "opacity_0_12_ratio",
        "line_height_1_0_ratio",
        "stroke_1_5_ratio",
        "stroke_3_0_ratio",
        "font_weight_400_0_ratio",
        "line_height_1_4_ratio",
        "line_height_1_2_ratio",
        "opacity_0_65_ratio",
        "opacity_0_25_ratio",
        "opacity_0_8_ratio",
        "stroke_0_7_ratio",
        "font_weight_550_0_ratio",
        "font_weight_450_0_ratio",
        "opacity_0_85_ratio",
        "stroke_1_2_ratio",
        "opacity_0_75_ratio",
        "line_height_1_25_ratio",
        "opacity_0_45_ratio",
        "opacity_0_32_ratio",
        "opacity_0_48_ratio",
        "opacity_0_26_ratio",
        "opacity_0_36_ratio",
        "opacity_0_3_ratio",
        "preset_columns_wide",
        "preset_columns_medium",
        "preset_columns_narrow",
        "body_line_height",
        "curve_dash_length_px",
        "curve_dash_gap_px",
        "guide_dash_length_px",
        "guide_dash_gap_px",
        "threshold_dash_length_px",
        "threshold_dash_gap_px",
        "command_wait_minimum_scale",
        "raised_surface_lightening_dark",
        "raised_surface_lightening_light",
        "section_surface_lightening_dark",
        "section_surface_lightening_light",
        "recessed_surface_blend",
        "control_surface_lightening_dark",
        "control_surface_lightening_light",
        "notice_tint",
        "heater_tint",
        "main_phase_tint",
        "section_phase_tint",
        "pending_command_tint",
        "color_mix_72_0_percent",
        "color_mix_20_0_percent",
        "color_mix_13_0_percent",
        "color_mix_10_0_percent",
        "color_mix_22_0_percent",
        "color_mix_16_0_percent",
        "color_mix_12_0_percent",
        "color_mix_24_0_percent",
        "color_mix_18_0_percent",
        "chart_coordinate_precision",
        "tick_epsilon",
        "tick_precision",
        "history_marker_focus_span_ms",
        "history_event_group_interval_ms",
        "event_marker_fallback_count",
        "diagnostic_wide_columns",
        "history_plot_viewport_height_percent",
        "history_compact_viewport_height_percent",
        "event_line_dash_length_px",
        "event_line_dash_gap_px",
        "ventilation_dash_length_px",
        "ventilation_dash_gap_px",
        "marker_link_dash_length_px",
        "marker_link_dash_gap_px",
        "instrument_rim_highlight_opacity",
        "instrument_rim_mid_opacity",
        "instrument_rim_shade_opacity",
        "instrument_value_arc_width_px",
        "instrument_handle_radius_px",
        "instrument_minor_tick_opacity",
        "instrument_tick_inset_px",
        "instrument_minor_tick_length_px",
        "instrument_major_tick_length_px",
        "instrument_tick_label_inset_px",
        "diagnostic_marker_point_radius_px",
        "diagnostic_marker_event_radius_px",
        "detail_single_point_radius_px",
        "diagnostic_axis_left_padding_px",
        "diagnostic_axis_right_padding_px",
        "diagnostic_axis_top_padding_px",
        "diagnostic_axis_bottom_padding_px",
        "diagnostic_guide_top_padding_px",
        "diagnostic_time_label_bottom_padding_px",
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


def _resolve_catalog_value(catalog, value, seen=()):
    """Resolve named aliases without maintaining copies of factory values."""
    if isinstance(value, dict):
        if set(value) in ({"preference"}, {"parameter"}):
            kind, key = next(iter(value.items()))
            identity = (kind, key)
            if not isinstance(key, str) or identity in seen:
                raise ValueError("defaults.json: invalid or cyclic value reference")
            if kind == "preference":
                definitions = catalog["appearance"]["preferences"]
            else:
                definitions = {item["key"]: item for item in catalog["parameters"]}
            if key not in definitions:
                raise ValueError(f"defaults.json: unknown {kind} reference {key}")
            return _resolve_catalog_value(
                catalog, definitions[key]["default"], (*seen, identity)
            )
        return {
            key: _resolve_catalog_value(catalog, item, seen)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_resolve_catalog_value(catalog, item, seen) for item in value]
    return deepcopy(value)


def preference_values(value, *, catalog=None, reject_catalog_overrides=False):
    """One validation path for factory and saved display preferences."""
    source = catalog if catalog is not None else _CATALOG
    definitions = source["appearance"]["preferences"]
    if not isinstance(value, dict) or value.keys() - definitions.keys():
        raise ValueError("Unknown display preferences")
    result = {}
    for key, definition in definitions.items():
        if definition["configuration_surface"] == "catalog":
            number = definition["default"]
            if reject_catalog_overrides and key in value:
                supplied = _number(value[key], key)
                if supplied != number:
                    raise ValueError(f"Catalog display preference is read-only: {key}")
        else:
            number = value.get(key, definition["default"])
        number = _number(number, key)
        if not definition["minimum"] <= number <= definition["maximum"]:
            raise ValueError(f"Display preference outside bounds: {key}")
        if definition["integer"] and not float(number).is_integer():
            raise ValueError(f"Display preference requires an integer: {key}")
        result[key] = int(number) if definition["integer"] else number
    for lower, upper in (
        ("history_axis_min_ticks", "history_axis_max_ticks"),
        ("instrument_tick_target", "instrument_tick_maximum"),
    ):
        if result[lower] > result[upper]:
            raise ValueError(f"Incompatible display preferences: {lower}, {upper}")
    if (
        result["history_plot_width_px"]
        <= result["history_plot_side_padding_px"] * 2
        or result["history_plot_height_px"]
        <= result["history_plot_top_padding_px"] + result["history_plot_bottom_padding_px"]
    ):
        raise ValueError("History plot padding leaves no drawing area")
    if (
        result["detail_plot_width_px"]
        <= result["detail_plot_side_padding_px"] + result["detail_plot_right_padding_px"]
        or result["detail_plot_height_px"]
        <= result["detail_plot_top_padding_px"] + result["detail_plot_bottom_padding_px"]
        or result["detail_plot_height_px"]
        <= max(
            result["detail_axis_top_padding_px"],
            result["detail_value_label_top_padding_px"],
        )
        + result["detail_axis_bottom_padding_px"]
        or result["detail_plot_height_px"] <= result["detail_time_label_bottom_padding_px"]
    ):
        raise ValueError("Detail plot padding leaves no drawing area")
    if (
        result["diagnostic_min_plot_width_px"]
        <= result["diagnostic_axis_left_padding_px"] + result["diagnostic_axis_right_padding_px"]
        or result["diagnostic_plot_height_px"]
        <= result["diagnostic_axis_top_padding_px"] + result["diagnostic_axis_bottom_padding_px"]
        or result["diagnostic_plot_height_px"]
        <= result["diagnostic_guide_top_padding_px"] + result["diagnostic_axis_bottom_padding_px"]
        or result["diagnostic_plot_height_px"]
        <= result["diagnostic_time_label_bottom_padding_px"]
    ):
        raise ValueError("Diagnostic plot padding leaves no drawing area")
    return result


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
        "configuration_surface",
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
            if (
                not isinstance(spec, dict)
                or not fields <= spec.keys()
                or spec.keys() - fields - {"minimum_parameter", "multiple_of_parameters"}
            ):
                raise ValueError("defaults.json: invalid parameter metadata")
            key = spec["key"]
            if not isinstance(key, str) or not key or key in definitions:
                raise ValueError("defaults.json: duplicate or invalid parameter key")
            section_keys.add(key)
            if spec["configuration_surface"] not in {"catalog", "integration", "panel"}:
                raise ValueError(f"defaults.json: invalid configuration surface for {key}")
            for name in ("label", "unit", "description", "group"):
                if not isinstance(spec[name], str) or not spec[name]:
                    raise ValueError(f"defaults.json: invalid {key}.{name}")
            for name in ("allow_zero", "optional", "integer", "expert"):
                if not isinstance(spec[name], bool):
                    raise ValueError(f"defaults.json: invalid {key}.{name}")  # noqa: TRY004 - uniform invalid-catalog ValueError contract.
            if spec["optional"] and key not in _OPTIONAL_PARAMETER_KEYS:
                raise ValueError(
                    f"defaults.json: invalid optional consumer value {key}"
                )
            if spec["settings_group"] not in group_ids:
                raise ValueError(f"defaults.json: unknown settings group for {key}")
            group_surface = next(
                group.get("surface", "panel") for group in groups
                if group["id"] == spec["settings_group"]
            )
            if spec["configuration_surface"] not in {"catalog", group_surface}:
                raise ValueError(f"defaults.json: conflicting configuration surface for {key}")
            if section_name == "legacy_parameters" and spec["configuration_surface"] != "catalog":
                raise ValueError(f"defaults.json: legacy standard is catalog-only: {key}")
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
            if isinstance(value, dict):
                if key != "target_temperature_c" or value != {
                    "parameter": "standard_temperature_c"
                }:
                    raise ValueError(f"defaults.json: invalid default reference for {key}")
            elif value is None:
                if not spec["optional"]:
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
    if isinstance(values["target_temperature_c"], dict):
        values["target_temperature_c"] = values["standard_temperature_c"]
        if values["target_temperature_c"] > definitions["target_temperature_c"]["maximum"]:
            raise ValueError("defaults.json: standard temperature exceeds target maximum")
    for key, definition in definitions.items():
        minimum_key = definition.get("minimum_parameter")
        if minimum_key is not None:
            if (
                not isinstance(minimum_key, str)
                or minimum_key not in definitions
                or definition["optional"]
                or definitions[minimum_key]["optional"]
                or values[key] is None
                or values[minimum_key] is None
                or values[key] < values[minimum_key]
            ):
                raise ValueError(f"defaults.json: invalid minimum reference for {key}")
        multiples = definition.get("multiple_of_parameters", [])
        if (
            not isinstance(multiples, list)
            or any(not isinstance(divisor, str) or divisor not in definitions
                   for divisor in multiples)
            or len(set(multiples)) != len(multiples)
        ):
            raise ValueError(f"defaults.json: invalid divisor references for {key}")
        for divisor in multiples:
            if (
                definition["optional"]
                or definitions[divisor]["optional"]
                or values[key] is None
                or values[divisor] is None
            ):
                raise ValueError(f"defaults.json: invalid divisor references for {key}")
            if values[divisor] <= 0 or values[key] % values[divisor]:
                raise ValueError(f"defaults.json: {key} must be a multiple of {divisor}")
    if values["curve_original_samples"] * 2 > values["curve_max_points"]:
        raise ValueError("defaults.json: curve_max_points must fit two original sample windows")
    if values["light_night_elevation_degrees"] >= values["light_day_elevation_degrees"]:
        raise ValueError("defaults.json: night elevation must be below day elevation")
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
    appearance = catalog["appearance"]
    _object(
        appearance,
        {"colors", "scales", "instruments", "precision", "preferences", "history_events"},
        "appearance",
    )
    _object(appearance["preferences"], _PREFERENCE_KEYS, "display preferences")
    for key, definition in appearance["preferences"].items():
        _object(
            definition,
            {"label", "description", "group", "default", "minimum", "maximum", "step", "integer", "unit", "configuration_surface"},
            key,
        )
        if definition["configuration_surface"] not in {"catalog", "panel"}:
            raise ValueError(f"defaults.json: invalid configuration surface for {key}")
        for name in ("label", "description", "group", "unit"):
            if not isinstance(definition[name], str) or not definition[name].strip():
                raise ValueError(f"defaults.json: invalid {key}.{name}")
        if not isinstance(definition["integer"], bool):
            raise ValueError(f"defaults.json: invalid {key}.integer")
        if (
            _number(definition["minimum"], key) > _number(definition["maximum"], key)
            or _number(definition["step"], key) <= 0
        ):
            raise ValueError(f"defaults.json: invalid display bounds for {key}")
    preference_values({}, catalog=catalog)
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
    for name, value in appearance["precision"].items():
        if value != {"preference": f"{name}_precision"}:
            raise ValueError("defaults.json: precision must reference its display preference")
        _integer(_resolve_catalog_value(catalog, value), "display precision")
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
    _object(appearance["history_events"], {"door", "infusion", "heater"}, "history events")
    for name, event in appearance["history_events"].items():
        _object(event, {"label", "default_visible", "color"}, f"history_events.{name}")
        if not isinstance(event["label"], str) or not event["label"].strip():
            raise ValueError(f"defaults.json: invalid history_events.{name}.label")
        if not isinstance(event["default_visible"], bool):
            raise ValueError(f"defaults.json: invalid history_events.{name}.default_visible")
        if not isinstance(event["color"], str) or event["color"] not in roles:
            raise ValueError(f"defaults.json: invalid history_events.{name}.color")
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
            reference = (
                {"parameter": key}
                if section_name == "runtime" or key == "climate_temperature_step_c"
                else {"preference": key}
            )
            if value != reference:
                raise ValueError(f"defaults.json: {key} must reference its leading definition")
            value = _resolve_catalog_value(catalog, value)
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
    return _resolve_catalog_value(_CATALOG, _CATALOG[name])


def instance_default(key, *, setup=False):
    """Return the instance default or its setup override."""
    values = (
        _CATALOG["setup"]
        if setup and key in _CATALOG["setup"]
        else _CATALOG["instance"]
    )
    return deepcopy(values[key])
