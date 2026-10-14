"""Reale Originalfolgen prüfen Türerholung bei belegtem Dauerheizen.

Der Heizkontext stammt aus dem ausgeführten R04. Dieser Ausschnitt prüft den
Detektor mit diesem Kontext, nicht die Folgen einer geänderten Heizsteuerung.
"""

from dataclasses import replace
from datetime import datetime, timedelta
import gzip
import json
from pathlib import Path
from time import perf_counter
import unittest

from test_detector import detection_parameters, measurement
from test_settings import fixture

from custom_components.ha_sauna.core.detector import Detector
from custom_components.ha_sauna.core.models import Position, Quantity
from custom_components.ha_sauna.core.timeline import Kind


class R04DoorRecoveryTests(unittest.TestCase):
    def test_first_two_door_sequences_recover_from_original_temperature(self):
        started = perf_counter()
        origin = datetime.fromisoformat(fixture("session")["origin"])

        def at(hour, minute, second=0):
            return origin.replace(hour=hour, minute=minute, second=second)

        begin, end = at(12, 57), at(13, 9)
        parameters = detection_parameters()
        observed = []
        detector = Detector(parameters, begin, observer=observed.append)
        # R04 actuator evidence: continuously ON 12:52:18.386868–13:24:17.414544.
        # Starting at the slice boundary gives no original before that boundary.
        detector.report_heating(True, begin)
        inputs = []
        path = Path(__file__).parent / "fixtures/replay/R04.samples.jsonl.gz"
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                received = origin + timedelta(seconds=row["t"])
                if received > end:
                    break
                if received < begin or row["role"] == "presence":
                    continue
                position, quantity = row["role"].split("_", 1)
                inputs.append(replace(
                    measurement(
                        Position(position), Quantity(quantity), row["value"], 0,
                        source=f"sensor.fixture_{row['role']}",
                    ),
                    received_at=received,
                ))

        events = []
        cursor = 0
        now = begin
        while now <= end:
            while cursor < len(inputs) and inputs[cursor].received_at <= now:
                self.assertTrue(detector.accept(inputs[cursor]))
                cursor += 1
            events.extend(detector.advance(now, enabled=True))
            now += timedelta(seconds=detector.grid_seconds)

        door_events = [
            event for event in events
            if event.kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE)
        ]
        # These are assessment intervals from the loss/recovery in the original
        # traces. They are not the old detector's second-exact trigger times.
        intervals = (
            (Kind.DOOR_OPEN, at(12, 58, 20), at(12, 58, 50), at(12, 59)),
            (Kind.DOOR_CLOSE, at(12, 58, 45), at(12, 59, 45), at(13, 0)),
            (Kind.DOOR_OPEN, at(13, 5, 30), at(13, 6, 10), at(13, 7)),
            (Kind.DOOR_CLOSE, at(13, 7, 35), at(13, 8, 25), end),
        )
        diagnostics = {
            "events": [
                (event.kind.value, event.effective_at, event.trace_at)
                for event in events
            ],
            "close_evidence": [
                {key: row[key] for key in (
                    "at", "signals", "conditions", "curve_candidates", "curve_proposals",
                )}
                for row in observed
                if row["at"] in (at(12, 59, 45), at(12, 59, 55), at(13, 8, 20), at(13, 8, 30))
            ],
        }
        print(
            f"R04 two-door slice: samples={cursor} ticks={len(observed)} "
            f"wall_seconds={perf_counter() - started:.6f} events={diagnostics['events']!r}",
        )
        self.assertEqual(
            [event.kind for event in door_events],
            [interval[0] for interval in intervals],
            msg=repr(diagnostics),
        )
        for event, (_, earliest, latest, confirmation_limit) in zip(door_events, intervals):
            with self.subTest(kind=event.kind, onset=event.effective_at):
                self.assertGreaterEqual(event.effective_at, earliest)
                self.assertLessEqual(event.effective_at, latest)
                self.assertLessEqual(event.trace_at, confirmation_limit)
                self.assertGreater(event.trace_at, event.effective_at)
                self.assertEqual(event.detected_at, event.trace_at)

                row = next(item for item in observed if item["at"] == event.trace_at)
                route = "door_close" if event.kind == Kind.DOOR_CLOSE else "door_open"
                routes = (route,) if route == "door_close" else (route, "door_open_heating")
                consumed = [
                    proposal for name in routes
                    for proposal in row["curve_proposals"].get(name, ())
                    if proposal["status"] == "consumed"
                    and proposal["confirmed_at"] == event.trace_at
                ]
                self.assertEqual(len(consumed), 1)
                package = consumed[0]
                self.assertEqual(package["consumed_onset_at"], event.effective_at)
                self.assertLess(package["first_observed_at"], event.trace_at)
                confirmations = {
                    item["candidate_id"]: item for item in package["confirmation"]
                }
                for witness in package["witnesses"]:
                    fresh = confirmations[witness["candidate_id"]]
                    self.assertTrue(fresh["supported"])
                    self.assertGreater(fresh["fresh_newest_at"], witness["first_newest_at"])
                    self.assertLessEqual(fresh["newest_at"], event.trace_at)
                    for stamp in (witness["onset_at"], fresh["fresh_newest_at"]):
                        self.assertTrue(any(
                            item.position.value == witness["position"]
                            and item.quantity.value == witness["quantity"]
                            and item.source == witness["source"]
                            and item.received_at == stamp
                            for item in inputs
                        ))
