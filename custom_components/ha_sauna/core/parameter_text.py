"""Compatibility view of the leading German text in defaults.json."""

from .defaults import section

PARAMETER_TEXT = {
    spec["key"]: (spec["label"], spec["description"], spec["group"])
    for spec in (*section("parameters"), *section("legacy_parameters"))
}
