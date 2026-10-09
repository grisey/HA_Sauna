"""Instrument preferences normalize older entries and preserve explicit choices."""

import unittest

from custom_components.ha_sauna.appearance import (
    APPEARANCE_CATALOG,
    default_appearance,
    validate_appearance,
)


class AppearanceTests(unittest.TestCase):
    def test_legacy_payload_uses_catalog_instrument_defaults(self):
        expected = {
            name: spec["default"]
            for name, spec in APPEARANCE_CATALOG["instruments"].items()
        }
        self.assertEqual(validate_appearance({"colors": {}})["instruments"], expected)
        self.assertEqual(default_appearance()["instruments"], expected)

    def test_individual_choices_and_inheritance_survive_normalization(self):
        for common in ("round", "linear"):
            for style in ("inherit", "round", "linear"):
                with self.subTest(common=common, style=style):
                    selected = {"default": common, "temperature": style,
                                "humidity": style, "light": style}
                    canonical = validate_appearance({"instruments": selected})
                    self.assertEqual(canonical["instruments"], selected)
                    self.assertEqual(validate_appearance(canonical), canonical)
                    selected["light"] = "changed"
                    self.assertEqual(canonical["instruments"]["light"], style)

    def test_partial_preferences_and_returned_defaults_are_independent(self):
        value = validate_appearance({"instruments": {"humidity": "linear"}})
        self.assertEqual(value["instruments"]["humidity"], "linear")
        self.assertEqual(value["instruments"]["temperature"],
                         APPEARANCE_CATALOG["instruments"]["temperature"]["default"])
        value["instruments"]["default"] = "linear"
        self.assertEqual(default_appearance()["instruments"]["default"],
                         APPEARANCE_CATALOG["instruments"]["default"]["default"])

    def test_invalid_instrument_preferences_are_rejected(self):
        for instruments in (
            None, [], "round", {"default": "inherit"}, {"unknown": "round"},
            {"temperature": True}, {"humidity": []}, {"light": "circular"},
        ):
            with self.subTest(instruments=instruments), self.assertRaises(ValueError):
                validate_appearance({"instruments": instruments})
