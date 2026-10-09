"""Validated, instance-specific panel appearance preferences and their catalog."""

import math
import re
from collections.abc import Mapping


from .core.defaults import section


APPEARANCE_CATALOG = section("appearance")
COLOR_DEFINITIONS = APPEARANCE_CATALOG["colors"]
COLOR_ROLES = frozenset(definition["id"] for definition in COLOR_DEFINITIONS)
SCALE_DEFAULTS = {
    name: dict(spec["default"]) for name, spec in APPEARANCE_CATALOG["scales"].items()
}
INSTRUMENT_DEFINITIONS = APPEARANCE_CATALOG["instruments"]
INSTRUMENT_DEFAULTS = {
    name: spec["default"] for name, spec in INSTRUMENT_DEFINITIONS.items()
}
_COLOR = re.compile(r"#[0-9A-Fa-f]{6}\Z")


def default_appearance():
    """Return independent mutable defaults for a new or older config entry."""
    return {
        "colors": {},
        "scales": {name: dict(bounds) for name, bounds in SCALE_DEFAULTS.items()},
        "instruments": dict(INSTRUMENT_DEFAULTS),
    }


def validate_appearance(value):
    """Validate a complete replacement; return a detached canonical value."""
    if not isinstance(value, Mapping) or set(value) - {"colors", "scales", "instruments"}:
        raise ValueError("Ungültige Darstellungseinstellungen")
    colors = value.get("colors", {})
    scales = value.get("scales", {})
    instruments = value.get("instruments", {})
    if not isinstance(instruments, Mapping) or set(instruments) - set(INSTRUMENT_DEFAULTS):
        raise ValueError("Ungültige Instrumentendarstellung")
    validated_instruments = dict(INSTRUMENT_DEFAULTS)
    for name, style in instruments.items():
        if not isinstance(style, str) or style not in INSTRUMENT_DEFINITIONS[name]["options"]:
            raise ValueError("Ungültige Instrumentendarstellung")
        validated_instruments[name] = style
    if not isinstance(colors, Mapping) or set(colors) - COLOR_ROLES:
        raise ValueError("Ungültige Farbrolle")
    if not isinstance(scales, Mapping) or set(scales) - set(SCALE_DEFAULTS):
        raise ValueError("Ungültige Diagrammskala")
    validated_colors = {}
    for role, color in colors.items():
        if not isinstance(color, str) or _COLOR.fullmatch(color) is None:
            raise ValueError("Farben müssen im Format #RRGGBB angegeben werden")
        validated_colors[role] = color
    validated_scales = {}
    for name, defaults in SCALE_DEFAULTS.items():
        bounds = scales.get(name, {})
        if not isinstance(bounds, Mapping) or set(bounds) - {"minimum", "maximum"}:
            raise ValueError("Ungültige Diagrammskala")
        minimum = bounds.get("minimum", defaults["minimum"])
        maximum = bounds.get("maximum", defaults["maximum"])
        valid_numbers = True
        finite_bounds = []
        for number in (minimum, maximum):
            if isinstance(number, bool) or not isinstance(number, (int, float)):
                valid_numbers = False
                break
            try:
                finite_number = float(number)
                if not math.isfinite(finite_number):
                    valid_numbers = False
                    break
                finite_bounds.append(finite_number)
            except OverflowError:
                valid_numbers = False
                break
        if (
            not valid_numbers
            or minimum >= maximum
            or not math.isfinite(finite_bounds[1] - finite_bounds[0])
        ):
            raise ValueError("Diagrammminimum muss kleiner als Maximum sein")
        if (
            minimum < APPEARANCE_CATALOG["scales"][name].get("minimum", minimum)
            or maximum > APPEARANCE_CATALOG["scales"][name].get("maximum", maximum)
        ):
            raise ValueError("Diagrammskala liegt außerhalb des zulässigen Bereichs")
        validated_scales[name] = {"minimum": minimum, "maximum": maximum}
    return {
        "colors": validated_colors,
        "scales": validated_scales,
        "instruments": validated_instruments,
    }
