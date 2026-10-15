"""Complete panel copies reuse real preview outputs and public projection."""

import json
import unittest
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields, replace
from unittest.mock import patch

from custom_components.ha_sauna.archive import plain
from custom_components.ha_sauna.core.model_handoff import HandoffContext, ModelHandoff
from custom_components.ha_sauna.core.presentation_handoff import (
    PanelSnapshot,
    PublicPanelSnapshot,
)
from custom_components.ha_sauna.core.system_handoff import to_wire
from custom_components.ha_sauna.presentation import public_state
from test_foundation import T0
from tools.local_preview import Preview


def panel_result(scenario="bereit"):
    """Use the existing runtime fixture, supplying its HTTP-only envelope.

    Preview supplies the actual controller, configuration, display and public
    presentation results. Its three simulation fields are outside StateView's
    contract; title/instance identity and archive errors belong to HTTP/runtime.
    """
    preview = Preview()
    preview.reset(scenario)
    result = preview.state()
    for key in ("preview_scenario", "preview_button", "preview_feedback"):
        result.pop(key)
    result.update(
        title="Synthetic panel",
        instances=[{"entry_id": "entry", "title": "Synthetic panel"}],
        archive_error=None,
    )
    return preview, result


class PanelSnapshotTests(unittest.TestCase):
    def test_visible_instance_references_do_not_change_snapshot_ownership(self):
        _preview, result = panel_result()
        result["instances"].append({"entry_id": "other", "title": "Other sauna"})
        snapshot = PanelSnapshot.from_result(result)
        handoff = ModelHandoff(HandoffContext("entry", "panel", T0), None, snapshot)
        self.assertEqual(handoff.after.to_wire()["instances"], result["instances"])

    def test_complete_existing_results_roundtrip_without_owner_evaluation(self):
        preview, result = panel_result()
        expected = deepcopy(result)
        with (
            patch.object(preview, "state", side_effect=AssertionError("No new state read")),
            patch.object(preview.c, "phase_projection",
                         side_effect=AssertionError("No new phase projection")),
            patch("tools.local_preview.phase_timer",
                  side_effect=AssertionError("No new display calculation")),
        ):
            snapshot = PanelSnapshot.from_result(result)
            self.assertEqual(snapshot.to_wire(), expected)
        self.assertEqual(snapshot.instances[0].entry_id, "entry")
        self.assertEqual(snapshot.start_availability.message,
                         expected["start_availability"]["message"])
        self.assertEqual(snapshot.environment.forecast_time,
                         expected["environment"]["forecast_time"])
        ModelHandoff(HandoffContext("entry", "panel", T0), None, snapshot)
        result["configuration"]["bindings"].clear()
        result["manual_controls"]["light"]["observation"]["available"] = False
        result["parameters"][0]["description"] = "Changed later"
        result["instances"][0]["title"] = "Changed later"
        self.assertEqual(snapshot.to_wire(), expected)
        with self.assertRaises(FrozenInstanceError):
            snapshot.permissions.admin = False
        with self.assertRaises(TypeError):
            snapshot.manual_controls.light.observation["available"] = False

    def test_missing_or_unrepresented_top_level_fields_fail_closed(self):
        _preview, result = panel_result()
        missing = dict(result)
        missing.pop("archive_revision")
        with self.assertRaises(TypeError):
            PanelSnapshot.from_result(missing)
        with self.assertRaises(TypeError):
            PanelSnapshot.from_result({**result, "future_top_level_field": True})

    def test_extensible_existing_maps_keep_new_fields_and_explicit_null(self):
        _preview, result = panel_result()
        result["detector_trace"] = {"future_metric": {"values": [1, None, 3]}}
        result["configuration_model"]["new_metadata"] = {"null": None}
        snapshot = PanelSnapshot.from_result(result)
        self.assertEqual(snapshot.to_wire(), result)
        result["detector_trace"]["future_metric"]["values"].append(4)
        self.assertEqual(snapshot.detector_trace["future_metric"]["values"], (1, None, 3))

    def test_existing_frozen_models_remain_the_supplied_models(self):
        preview, result = panel_result()
        snapshot = PanelSnapshot.from_result(result)
        projection = preview.c.phase_projection(preview.now)
        model_snapshot = replace(
            snapshot,
            now=preview.now,
            session=preview.c.session,
            rule_inputs=preview.c.regulation_inputs,
            phase_projection=projection,
            decision=preview.c.last_decision,
        )
        self.assertIs(model_snapshot.session, preview.c.session)
        self.assertIs(model_snapshot.phase_projection, projection)
        self.assertEqual(model_snapshot.to_wire(), snapshot.to_wire())
        ModelHandoff(HandoffContext("entry", "typed-panel", T0), None, model_snapshot)

    def test_existing_phase_serialization_retains_derived_wire_fields(self):
        preview = Preview()
        preview.reset("verlauf")
        phase = preview.c.session.after_run
        self.assertIsNotNone(phase)
        # TimedPhase has derived wire fields owned solely by archive.plain.
        self.assertEqual(to_wire(phase), plain(phase))
        self.assertIn("required_seconds", to_wire(phase))
        self.assertIn("remaining_seconds", to_wire(phase))

    def test_public_projection_uses_the_existing_policy_once_and_preserves_absence(self):
        _preview, result = panel_result()
        result["permissions"]["admin"] = False
        snapshot = PanelSnapshot.from_result(result)
        expected = public_state(deepcopy(result))
        with patch(
            "custom_components.ha_sauna.presentation.public_state",
            wraps=public_state,
        ) as project:
            public = PublicPanelSnapshot.from_snapshot(snapshot)
            project.assert_called_once_with(result)
        self.assertEqual(public.to_wire(), expected)
        self.assertEqual(to_wire(public), expected)
        self.assertEqual({field.name for field in fields(public)}, set(expected))
        self.assertFalse(hasattr(public, "presence"))
        self.assertFalse(hasattr(public, "decision"))
        self.assertFalse(hasattr(public, "archive_error"))
        self.assertNotIn("bindings", public.configuration)
        self.assertIsNone(public.last_session)
        self.assertFalse(public.permissions.admin)
        self.assertEqual(public.to_wire()["environment"], result["environment"])
        self.assertEqual(snapshot.to_wire(), result)
        ModelHandoff(HandoffContext("entry", "public-panel", T0), None, public)

    def test_already_public_result_is_copied_without_rerunning_projection(self):
        _preview, result = panel_result()
        result = public_state(result)
        expected = deepcopy(result)
        with patch(
            "custom_components.ha_sauna.presentation.public_state",
            side_effect=AssertionError("Already projected"),
        ):
            public = PublicPanelSnapshot.from_result(result)
        result["environment"]["values"].clear()
        self.assertEqual(public.to_wire(), expected)
        self.assertNotIn("rule_inputs", public.to_wire())

    def test_public_contract_rejects_missing_extra_and_private_top_level_fields(self):
        _preview, result = panel_result()
        projected = public_state(result)
        missing = dict(projected)
        missing.pop("last_session")
        with self.assertRaises(TypeError):
            PublicPanelSnapshot.from_result(missing)
        for extra in ({"future_top_level_field": True}, {"decision": None}):
            with self.subTest(extra=extra), self.assertRaises(TypeError):
                PublicPanelSnapshot.from_result({**projected, **extra})

    def test_public_snapshot_keeps_original_reference_projection_without_private_sources(self):
        _preview, result = panel_result("gang")
        result["measurements"][0]["source"] = "sensor.private_handoff_source"
        snapshot = PanelSnapshot.from_result(result)
        public = PublicPanelSnapshot.from_snapshot(snapshot)
        expected = public_state(deepcopy(result))
        self.assertEqual(public.to_wire(), expected)
        self.assertNotIn("configuration", public.session)
        self.assertNotIn("source", public.measurements[0])
        encoded = json.dumps(public.to_wire())
        self.assertNotIn("sensor.private_handoff_source", encoded)
        self.assertNotIn("binary_sensor.demo_fp300", encoded)
        self.assertEqual(snapshot.to_wire(), result)
