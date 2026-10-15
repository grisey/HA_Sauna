"""Synthetic closed-output checks; no live publication or device evidence."""

import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta

from test_foundation import T0, parameters

from custom_components.ha_sauna.core.button import ButtonGestures
from custom_components.ha_sauna.core.detector import Detection, Detector
from custom_components.ha_sauna.core.light_output import (
    LightOutput,
    LightPlan,
    LightQuantizer,
)
from custom_components.ha_sauna.core.model_handoff import HandoffContext, ModelHandoff
from custom_components.ha_sauna.core.models import Measurement, Position, Quantity
from custom_components.ha_sauna.core.observation_handoff import (
    ButtonEvaluation,
    DetectionEvaluation,
    DeviceDiagnostics,
    DeviceObservation,
    HeaterServiceResult,
    HeatingObservation,
    InfusionEpisodeDiagnostics,
    LightOutputSnapshot,
    LightQuantization,
    LightQuantizerSnapshot,
    LightServiceResult,
    MethodEvaluation,
    ObservedSource,
)
from custom_components.ha_sauna.core.thermostat import Decision
from custom_components.ha_sauna.core.timeline import Kind


class ObservationHandoffTests(unittest.TestCase):
    def handoff(self, value, before=None):
        context = HandoffContext("entry", "observation", T0, session_id="session")
        result = ModelHandoff(context, before, value)
        self.assertIs(result.after, value)
        self.assertEqual(result.context.session_id, "session")
        return result

    def test_heating_observation_preserves_complete_supplied_feedback(self):
        source = {
            "source": "power", "heating": False, "power_w": 0,
            "contactor": True, "estimated": False,
        }
        observed = HeatingObservation.from_result(source)
        self.handoff(observed)
        source["heating"] = True
        self.assertIs(observed.heating, False)
        self.assertIs(observed.estimated, False)
        self.assertEqual(observed.power_w, 0)
        unknown = HeatingObservation.from_result({
            "source": "unknown", "heating": None, "power_w": None,
            "contactor": None, "estimated": False,
        })
        self.handoff(unknown, observed)
        self.assertIsNone(unknown.heating)
        self.assertIsNone(unknown.contactor)
        fallback = HeatingObservation.from_result({
            "source": "contactor", "heating": False, "power_w": None,
            "contactor": False, "estimated": True,
        })
        self.handoff(fallback, unknown)
        self.assertIs(fallback.contactor, False)
        self.assertTrue(fallback.estimated)

    def test_heating_observation_rejects_placeholder_and_wrong_field_types(self):
        complete = {
            "source": "power", "heating": False, "power_w": 0,
            "contactor": False, "estimated": False,
        }
        with self.assertRaises(ValueError):
            HeatingObservation.from_result({
                "source": "unknown", "heating": None, "power_w": None,
            })
        for name in complete:
            incomplete = {key: value for key, value in complete.items() if key != name}
            with self.subTest(missing=name), self.assertRaises(ValueError):
                HeatingObservation.from_result(incomplete)
        for change in (
            {"source": None}, {"heating": 0}, {"power_w": False},
            {"contactor": "off"}, {"estimated": None},
        ):
            with self.subTest(change=change), self.assertRaises((TypeError, ValueError)):
                HeatingObservation.from_result({**complete, **change})
        with self.assertRaises(ValueError):
            HeatingObservation.from_result({**complete, "extra": True})

    def test_detector_output_keeps_full_diagnostic_and_actual_detections(self):
        detector = Detector(parameters(), T0)
        detections = detector.advance(T0, enabled=True)
        result = DetectionEvaluation(detector.diagnostic, detections)
        self.handoff(result)
        self.assertEqual(result.diagnostic["at"], detector.diagnostic["at"])
        self.assertEqual(result.detections, tuple(detections))
        original_checks = dict(result.diagnostic["checks"])
        detector.diagnostic["checks"]["later"] = True
        self.assertEqual(dict(result.diagnostic["checks"]), original_checks)
        detection = Detection(Kind.DOOR_OPEN, T0, T0, ("upper",), T0)
        with self.assertRaises(ValueError):
            DetectionEvaluation(result.diagnostic, [detection])
        with self.assertRaises((TypeError, AttributeError)):
            result.diagnostic["checks"]["later"] = False

    def test_detection_evaluation_requires_complete_matching_raster_evidence(self):
        detector = Detector(parameters(), T0)
        detector.advance(T0, enabled=True)
        diagnostic = dict(detector.diagnostic)
        detection = Detection(Kind.DOOR_OPEN, T0, T0, ("upper",), T0)
        diagnostic["signals"] = (detection.kind,)
        matched = DetectionEvaluation(diagnostic, (detection,))
        self.handoff(matched)
        self.assertIs(matched.detections[0], detection)
        with self.assertRaises(ValueError):
            DetectionEvaluation({}, ())
        for key in diagnostic:
            incomplete = {name: value for name, value in diagnostic.items() if name != key}
            with self.subTest(missing=key), self.assertRaises(ValueError):
                DetectionEvaluation(incomplete, (detection,))
        later = replace(detection, trace_at=T0 + timedelta(seconds=1))
        with self.assertRaises(ValueError):
            DetectionEvaluation(diagnostic, (later,))
        with self.assertRaises(ValueError):
            DetectionEvaluation(diagnostic, ())

    def test_method_evaluation_keeps_its_full_diagnostic_without_legacy_placeholders(self):
        diagnostic = {
            "fit": {"coefficients": [1.0, 2.0], "uncertainty": None},
            "complete_extra_output": {"retained": ["original"]},
        }
        detection = Detection(Kind.INFUSION, T0, T0, ("upper",), T0)
        evaluated_at = T0 + timedelta(seconds=2)
        result = MethodEvaluation(diagnostic, evaluated_at, (detection,))
        self.handoff(result)

        self.assertEqual(result.evaluated_at, evaluated_at)
        self.assertIs(result.detections[0], detection)
        self.assertEqual(set(result.diagnostic), set(diagnostic))
        self.assertNotIn("holds", result.diagnostic)
        self.assertNotIn("checks", result.diagnostic)
        self.assertNotIn("infusion_episode", result.diagnostic)
        diagnostic["fit"]["coefficients"].append(3.0)
        diagnostic["complete_extra_output"]["retained"][0] = "changed"
        self.assertEqual(result.diagnostic["fit"]["coefficients"], (1.0, 2.0))
        self.assertEqual(
            result.diagnostic["complete_extra_output"]["retained"], ("original",)
        )

        with self.assertRaises(TypeError):
            MethodEvaluation(object(), evaluated_at, ())
        with self.assertRaises(TypeError):
            self.handoff(MethodEvaluation({}, [], ()))
        no_episode = MethodEvaluation({"infusion_episode": None}, evaluated_at, ())
        self.handoff(no_episode)
        self.assertIsNone(no_episode.diagnostic["infusion_episode"])

    def test_episode_projection_retains_unknown_end_interruption_and_late_continuation(self):
        projection = {
            "phase": "active",
            "onset_at": T0,
            "confirmed_at": T0 + timedelta(seconds=5),
            "level_end_at": None,
            "end_basis": None,
            "interrupted": False,
            "water_change": {"upper": 1.5},
            "decays": {"upper": T0 + timedelta(seconds=4)},
            "levels": {
                "upper": {
                    "first_newest_at": T0 + timedelta(seconds=6),
                    "fresh_newest_at": None,
                    "newest_at": T0 + timedelta(seconds=6),
                    "start_at": T0 + timedelta(seconds=5),
                }
            },
        }
        active = InfusionEpisodeDiagnostics.from_result(projection)
        interrupted = InfusionEpisodeDiagnostics.from_result({
            **projection, "interrupted": True,
        })
        ended = InfusionEpisodeDiagnostics.from_result({
            **projection,
            "phase": "ended",
            "level_end_at": T0 + timedelta(seconds=10),
            "end_basis": "decay_and_level",
        })
        continued = InfusionEpisodeDiagnostics.from_result(projection)
        self.handoff(active)
        self.handoff(interrupted, active)
        self.handoff(ended, interrupted)
        correction = self.handoff(continued, ended)

        self.assertIsNone(interrupted.level_end_at)
        self.assertIsNone(interrupted.end_basis)
        self.assertTrue(interrupted.interrupted)
        self.assertEqual(correction.after.onset_at, correction.before.onset_at)
        self.assertEqual(correction.after.confirmed_at, correction.before.confirmed_at)
        self.assertEqual(correction.before.phase, "ended")
        self.assertEqual(correction.after.phase, "active")
        self.assertIsNone(correction.after.level_end_at)
        projection["water_change"]["upper"] = 99.0
        projection["levels"]["upper"]["start_at"] = T0
        self.assertEqual(active.water_change["upper"], 1.5)
        self.assertEqual(
            active.levels["upper"]["start_at"], T0 + timedelta(seconds=5)
        )
        with self.assertRaises(ValueError):
            InfusionEpisodeDiagnostics.from_result({"phase": "active"})
        for change in (
            {"phase": []},
            {"onset_at": []},
            {"confirmed_at": "not a timestamp"},
            {"level_end_at": []},
            {"end_basis": []},
            {"interrupted": 1},
        ):
            with self.subTest(change=change), self.assertRaises(TypeError):
                self.handoff(
                    InfusionEpisodeDiagnostics.from_result({**projection, **change})
                )

    def test_light_snapshot_reads_curve_override_and_plan_without_advancing(self):
        output = LightOutput(parameters())
        end = T0 + timedelta(seconds=120)
        output.update(T0, "phase", "nachlauf", 40, 40, end)
        output.set_manual(55, phase_key="phase", ends_at=end)
        plan = output.update(T0, "phase", "nachlauf", 40, 40, end)
        before = dict(vars(output))
        snapshot = LightOutputSnapshot.from_output(output, plan)
        self.handoff(snapshot)
        self.assertEqual(vars(output), before)
        self.assertIs(snapshot.plan, plan)
        self.assertEqual(snapshot.manual_brightness, 55)
        self.assertEqual(snapshot.manual_ends_at, end)
        self.assertEqual(snapshot.phase_key, "phase")
        self.assertIsNotNone(snapshot.motion)
        initial = snapshot.motion.start_percent
        output._motion.start_percent = initial + 1
        self.assertEqual(snapshot.motion.start_percent, initial)
        with self.assertRaises(FrozenInstanceError):
            snapshot.motion.start_percent = 0
        output.return_to_automatic()
        plan = output.update(
            T0 + timedelta(seconds=1), "phase", "nachlauf", 40, 55, end
        )
        returning = LightOutputSnapshot.from_output(output, plan)
        self.handoff(returning, snapshot)
        self.assertIsNone(returning.manual_brightness)
        self.assertEqual(returning.resume_pending, output._resume_pending)
        if output._return_transition is not None:
            self.assertEqual(
                returning.return_transition.duration_seconds,
                output._return_transition.duration_seconds,
            )

    def test_button_evaluation_preserves_complete_returned_tuple(self):
        button = ButtonGestures(timedelta(seconds=1))
        for gesture in ("on", "off", "double"):
            actions = button.handle_actions(gesture, False, T0)
            result = ButtonEvaluation(gesture, False, T0, True, actions)
            self.handoff(result, result)
            self.assertEqual(result.actions, actions)
        action = button.advance(T0, False)
        result = ButtonEvaluation(
            None, False, T0, True, () if action is None else (action,)
        )
        self.handoff(result)
        self.assertIsNone(result.normalized_input)
        self.assertEqual(result.actions, () if action is None else (action,))

    def test_quantization_keeps_actual_consecutive_memory_and_reset_results(self):
        quantizer = LightQuantizer()
        previous_handoff = None
        for plan in (
            LightPlan(30.6, True, False),
            LightPlan(30.4, True, False),
            LightPlan(0, True, False),
        ):
            previous = quantizer._previous
            result = quantizer.quantize(plan, 1)
            before_capture = quantizer._previous
            observed = LightQuantization(plan, previous, quantizer._previous, 1, result)
            handoff = self.handoff(observed, previous_handoff)
            self.assertEqual(quantizer._previous, before_capture)
            self.assertIs(handoff.after.plan, plan)
            self.assertEqual(observed.previous_value, previous)
            self.assertEqual(observed.result, result)
            previous_handoff = observed
        self.assertEqual(observed.result, 0)
        self.assertIsNone(observed.next_value)

    def test_quantizer_reset_is_observable_without_another_plan(self):
        quantizer = LightQuantizer()
        quantizer.quantize(LightPlan(30.6, True, False), 1)
        before = LightQuantizerSnapshot.capture(quantizer)
        quantizer.reset()
        after = LightQuantizerSnapshot.capture(quantizer)
        handoff = self.handoff(after, before)
        self.assertIsNotNone(handoff.before.previous_value)
        self.assertIsNone(handoff.after.previous_value)
        self.assertIsNone(quantizer._previous)

    def test_quantization_checks_its_own_public_boundary(self):
        value = LightQuantization(LightPlan(30, True, False), None, 30, 1, 30)
        for change in (
            {"plan": None}, {"plan": LightPlan("30", True, False)},
            {"previous_value": True}, {"next_value": 30.5},
            {"result": None}, {"result": False}, {"hysteresis_percent": []},
        ):
            with self.subTest(change=change), self.assertRaises(TypeError):
                replace(value, **change)

    def test_device_sources_and_measurements_are_detached_from_adapter_data(self):
        attributes = {"unit_of_measurement": "°C", "nested": {"values": [1, 2]}}
        context = {"id": "synthetic", "user_id": None, "parent_id": None}
        source = ObservedSource(
            "sensor.synthetic", "40", attributes, T0, T0, T0, context
        )
        measurement = Measurement(
            Position.UPPER, Quantity.TEMPERATURE, 40, "40", "sensor.synthetic", T0
        )
        sources = [source]
        result = DeviceObservation(sources, [measurement])
        self.handoff(result)
        attributes["nested"]["values"].append(3)
        context["user_id"] = "changed"
        sources.clear()
        self.assertEqual(result.sources, (source,))
        self.assertIs(result.measurements[0], measurement)
        self.assertEqual(source.attributes["nested"]["values"], (1, 2))
        self.assertIsNone(source.context["user_id"])
        missing = replace(source, state=None, last_changed=None, last_updated=None)
        self.assertIsNone(missing.state)

    def test_diagnostics_keep_onset_and_service_error_without_inventing_ack(self):
        faults = {"heater_service_unavailable": "confirmed"}
        since = {"heater_service_unavailable": T0}
        result = DeviceDiagnostics(faults, since, True, T0)
        self.handoff(result)
        faults.clear()
        since.clear()
        self.assertEqual(result.fault_since["heater_service_unavailable"], T0)
        self.assertEqual(result.faults["heater_service_unavailable"], "confirmed")
        self.assertTrue(result.command_error)

    def test_complete_service_results_remain_separate_from_feedback(self):
        decision = Decision(T0, True, "synthetic", "session", T0)
        heater = HeaterServiceResult("switch.synthetic", True, decision, None, T0, T0)
        self.handoff(heater)
        self.assertIs(heater.decision, decision)
        self.assertFalse(heater.feedback_proves_heating)
        light = LightServiceResult(
            "light.synthetic", T0, "synthetic", "synthetic-phase", "turn_on", 30, 0, None,
            T0, T0 + timedelta(seconds=1), "SyntheticError",
        )
        self.handoff(light)
        self.assertEqual(light.service_error, "SyntheticError")
        self.assertEqual(light.completed_at, T0 + timedelta(seconds=1))
        off = replace(light, service="turn_off", brightness_pct=None, service_error=None)
        self.handoff(off, light)
        self.assertIsNone(off.brightness_pct)
        self.assertEqual(off.service, "turn_off")
        with self.assertRaises(ValueError):
            replace(heater, feedback_proves_heating=True)

    def test_invalid_nested_outputs_and_live_objects_are_rejected(self):
        with self.assertRaises(TypeError):
            DetectionEvaluation({}, [object()])
        with self.assertRaises(TypeError):
            DetectionEvaluation({"live": object()}, ())
        with self.assertRaises(TypeError):
            DeviceObservation([object()], [])
        with self.assertRaises(TypeError):
            DeviceObservation([], [object()])
        with self.assertRaises(TypeError):
            ButtonEvaluation("short", 1, T0, True, ())
        with self.assertRaises(TypeError):
            DeviceDiagnostics({}, {}, 1, None)
        with self.assertRaises(TypeError):
            HeaterServiceResult("switch.synthetic", True, object(), None, T0, T0)
        with self.assertRaises(TypeError):
            LightServiceResult("light.synthetic", T0, "p", "p", "turn_on", [], None, None, T0, T0, None)
