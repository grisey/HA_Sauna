"""Read-only capture contract on native types, not a cause proof for R16."""

import json
import unittest

import test_detector as fixtures
from test_cooling import controller
from test_foundation import bindings, event

from custom_components.ha_sauna.archive import plain
from custom_components.ha_sauna.core.detector import Detector, InfusionEpisodeUpdate
from custom_components.ha_sauna.core.models import Quantity
from custom_components.ha_sauna.core.timeline import Event, InfusionEnd, Kind
from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime
from sessions.infusion_failure_diagnostics import capture


class InfusionFailureDiagnosticsTests(unittest.TestCase):
    def test_native_failed_queue_is_serializable_and_capture_preserves_ownership(self):
        fixture = fixtures.InfusionLifecycleTests(methodName="runTest")
        fixture.setUp()
        points, _, next_rise, _ = fixture._scenario(merged=False)
        c = controller()
        c.process(event("capture-entry", Kind.DOOR_OPEN, 0))
        c.process(event("capture-close", Kind.DOOR_CLOSE, fixture.step))
        runtime = SaunaRuntime(Configuration(bindings(), fixture.parameters), lambda: fixtures.T0)
        runtime.controller = c
        detector = Detector(fixture.parameters, fixtures.T0)
        runtime.detector = detector
        runtime._detector_session = c.session.session_id
        received = []

        def book(detection):
            if detection.kind is not Kind.INFUSION:
                return
            received.append(detection)
            source = Event(
                f"capture-infusion:{len(received)}", c.session.session_id, detection.kind,
                detection.effective_at, detection.detected_at,
                booking_at=detection.trace_at, trace_at=detection.trace_at,
            )
            c.process(source, recognition_at=detection.trace_at)
            runtime._detector_infusion_events[id(detection)] = (detection, source.event_id)

        class DiagnosticStop(Exception):
            pass

        failure = DiagnosticStop("Keep the first real update unconsumed for capture")

        def stop(update):
            self.assertIsInstance(update, InfusionEpisodeUpdate)
            self.assertIsInstance(update.end, InfusionEnd)
            raise failure

        with self.assertRaises(DiagnosticStop) as raised:
            for second in range(0, next_rise, fixture.step):
                for position in detector.positions:
                    for quantity, value in (
                        (Quantity.TEMPERATURE, fixture.temperature),
                        (Quantity.HUMIDITY, fixture._value(points, second)),
                    ):
                        self.assertTrue(detector.accept(fixtures.measurement(
                            position, quantity, value, second, measured=fixture._at(second),
                        )))
                detector.advance(fixture._at(second), enabled=True,
                                 on_detection=book, on_infusion_update=stop)
        self.assertIs(raised.exception, failure)
        self.assertEqual(len(received), 1)
        self.assertEqual(len(detector._infusion_updates), 1)
        queued = detector._infusion_updates[0]
        self.assertIs(queued.confirmed_detection, received[0])

        def references():
            objects = [detector._infusion_updates, queued, queued.confirmed_detection,
                       queued.end, detector._infusion_episode, c.session]
            for state in detector._position_evidence.values():
                objects.extend((state, state["infusion"], state["infusion_candidate"]))
                episode = state["infusion"]
                if episode is None:
                    continue
                for name in ("original_start_component", "original_component"):
                    component = episode.get(name)
                    objects.append(component)
                    if component is not None:
                        objects.extend(pair for _, pair in component["binding_windows"])
                objects.extend(window for _, window in episode.get("original_end_windows", ()))
            return objects

        def contents():
            return json.dumps(plain({
                "queue": tuple(detector._infusion_updates),
                "global": detector._infusion_episode,
                "local": {str(position): {name: state[name] for name in (
                    "infusion", "infusion_candidate", "infusion_updates",
                )} for position, state in detector._position_evidence.items()},
                "source_mapping": runtime._detector_infusion_events,
                "session": c.session,
            }), sort_keys=True)

        held, before = references(), contents()
        snapshot = capture(runtime)
        encoded = json.dumps(plain(snapshot), sort_keys=True)
        decoded = json.loads(encoded)
        self.assertEqual(contents(), before)
        self.assertTrue(all(actual is original for actual, original
                            in zip(references(), held, strict=True)))
        self.assertIs(detector._infusion_updates[0], queued)
        self.assertIs(snapshot["failed_update"], queued)
        self.assertTrue(decoded["runtime_source"]["exact_detection_identity"])
        self.assertEqual(decoded["runtime_source"]["source_events"][0]["event_id"],
                         decoded["runtime_source"]["event_id"])
        self.assertEqual(decoded["failed_update"]["end"], plain(queued.end))
        self.assertTrue(decoded["global_owner"]["same_detection_as_failed_update"])
        self.assertTrue(decoded["global_owner"]["original_component"]["binding_windows"])
        self.assertTrue(decoded["global_owner"]["original_end_windows"])
        self.assertEqual(len(decoded["local_owners"]), len(detector.positions))


if __name__ == "__main__":
    unittest.main()
