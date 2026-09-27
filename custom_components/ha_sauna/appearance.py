"""Validated, instance-specific panel appearance preferences and their catalog."""

import json
import math
import re
from pathlib import Path
from collections.abc import Mapping


# JSON is the single catalog source shared with the standalone panel tests.
APPEARANCE_CATALOG = json.loads(
    Path(__file__).with_name("appearance_catalog.json").read_text(encoding="utf-8")
)
COLOR_DEFINITIONS = APPEARANCE_CATALOG["colors"]
COLOR_ROLES = frozenset(definition["id"] for definition in COLOR_DEFINITIONS)
SCALE_DEFAULTS = {
    name: dict(spec["default"]) for name, spec in APPEARANCE_CATALOG["scales"].items()
}
_COLOR = re.compile(r"#[0-9A-Fa-f]{6}\Z")


def default_appearance():
    """Return independent mutable defaults for a new or older config entry."""
    return {
        "colors": {},
        "scales": {name: dict(bounds) for name, bounds in SCALE_DEFAULTS.items()},
    }


def validate_appearance(value):
    """Validate a complete replacement; return a detached canonical value."""
    if not isinstance(value, Mapping) or set(value) - {"colors", "scales"}:
        raise ValueError("Ungültige Darstellungseinstellungen")
    colors = value.get("colors", {})
    scales = value.get("scales", {})
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
        for number in (minimum, maximum):
            if isinstance(number, bool) or not isinstance(number, (int, float)):
                valid_numbers = False
                break
            try:
                if not math.isfinite(float(number)):
                    valid_numbers = False
                    break
            except OverflowError:
                valid_numbers = False
                break
        if not valid_numbers or minimum >= maximum or not math.isfinite(maximum - minimum):
            raise ValueError("Diagrammminimum muss kleiner als Maximum sein")
        if name == "humidity" and (minimum < 0 or maximum > 100):
            raise ValueError("Feuchteskala muss zwischen 0 und 100 liegen")
        validated_scales[name] = {"minimum": minimum, "maximum": maximum}
    return {"colors": validated_colors, "scales": validated_scales}
