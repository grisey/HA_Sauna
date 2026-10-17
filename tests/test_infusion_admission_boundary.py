"""Admission-boundary variant of the existing two-pulse climate fixture.

Continuous measurements distinguish admission loss from the existing gap test.
No event, clock, original form or mathematical fit is mocked. This module uses
composition so importing the fixture does not rediscover its TestCase methods.
"""

import unittest

import test_detector as fixture_source

from custom_components.ha_sauna.core.detector import Detector
from custom_components.ha_sauna.core.models import Position, Quantity
from custom_components.ha_sauna.core.timeline import Kind


class InfusionAdmissionBoundaryTests(unittest.TestCase):
    def test_unconfirmed_pulse_cannot_cross_admission_loss_into_next_pulse(self):
        fixture = fixture_source.InfusionLifecycleTests(methodName="runTest")
        fixture.setUp()
        points, _, next_rise, next_fall = fixture._scenario(merged=False)
        detector = Detector(fixture.parameters, fixture_source.T0, (Position.UPPER,))
        events = []
        resume = next_rise - (2 * fixture.side + 1) * fixture.step

        def feed(through, *, admitted, stop_when=None):
            for second in range(
                (detector.index + 1) * fixture.step, through + fixture.step, fixture.step,
            ):
                humidity = fixture._value(points, second)
                for quantity, value in (
                    (Quantity.TEMPERATURE, fixture.temperature),
                    (Quantity.HUMIDITY, humidity),
                ):
                    self.assertTrue(detector.accept(fixture_source.measurement(
                        Position.UPPER, quantity, value, second,
                        measured=fixture._at(second),
                    )))
                events.extend(detector.advance(
                    fixture._at(second), enabled=True,
                    allowed=lambda kind: admitted or kind != Kind.INFUSION,
                ))
                if stop_when is not None and stop_when():
                    break

        # Target the first native local binding, before confirmation. The
        # uninterrupted curve still reaches both declared peaks and tails;
        # only the external admission loss is tied to this exact lifecycle.
        feed(fixture.begin + fixture.width, admitted=True, stop_when=lambda: (
            detector._position_state(Position.UPPER)["infusion_candidate"] is not None
        ))
        component = detector._position_state(Position.UPPER)["infusion_candidate"]
        self.assertIsNotNone(component, "First declared rise must bind a local native candidate")
        # Local binding precedes qualification and global owner adoption.
        # This contract needs the actual unconfirmed identity, not a public
        # episode that may not exist at this first observed binding.
        old_bound_at = component["received_at"]
        old_binding_windows = component["binding_windows"]
        self.assertFalse(component["fall_confirmed"])
        self.assertEqual(fixture._infusions(events), [])
        context = detector._original_context()
        feed(resume - fixture.step, admitted=False)
        self.assertIsNone(detector.infusion_episode)
        self.assertIsNone(detector._position_state(Position.UPPER)["infusion_candidate"])
        self.assertIsNone(detector._position_state(Position.UPPER)["infusion"])
        self.assertEqual(fixture._infusions(events), [])
        # Real originals continue throughout the blocked first peak and tail.
        for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY):
            self.assertEqual(detector.latest[Position.UPPER, quantity].measured_at,
                             fixture._at(resume - fixture.step))
        feed(next_fall, admitted=True)
        infusions = fixture._infusions(events)
        self.assertEqual(len(infusions), 1)
        event = infusions[0]
        self.assertGreaterEqual(event.effective_at, fixture._at(next_rise))
        self.assertGreater(event.trace_at, event.effective_at)
        self.assertEqual(event.channels, (Position.UPPER.value,))
        self.assertEqual(detector._original_context(), context)
        current = detector._position_state(Position.UPPER)["infusion_candidate"]
        self.assertIsNotNone(current)
        self.assertIsNot(current, component)
        self.assertNotEqual(current["received_at"], old_bound_at)
        self.assertGreaterEqual(current["received_at"], fixture._at(resume))
        self.assertNotEqual(current["binding_windows"], old_binding_windows)
        self.assertTrue(all(
            pair.temperature.start_at >= fixture._at(resume)
            and pair.humidity.start_at >= fixture._at(resume)
            for _, pair in current["binding_windows"]
        ))
        # A global episode is required only after the actual event was emitted.
        self.assertIsNotNone(detector.infusion_episode)
        self.assertGreaterEqual(detector.infusion_episode.bound_at, current["received_at"])
        self.assertIs(detector.infusion_episode.confirmed_detection, event)
