"""Reale Originalfolgen prüfen Türerholung bei belegtem Heizkontext.

Der Heizkontext stammt aus dem ausgeführten R04. Die Ausschnitte prüfen den
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
        opening_witnesses = {}
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
                if event.kind == Kind.DOOR_OPEN:
                    opening_witnesses = {
                        (witness["position"], witness["quantity"]): witness
                        for witness in package["witnesses"]
                    }
                self.assertEqual(package["consumed_onset_at"], event.effective_at)
                self.assertLess(package["first_observed_at"], event.trace_at)
                bound_at = package.get("evidence_bound_at")
                if event.kind == Kind.DOOR_CLOSE and package["quantities"] == ("temperature",):
                    self.assertIsNotNone(bound_at)
                    self.assertGreaterEqual(bound_at, package["first_observed_at"])
                    self.assertLessEqual(bound_at, event.trace_at)
                confirmations = {
                    item["candidate_id"]: item for item in package["confirmation"]
                }
                for witness in package["witnesses"]:
                    fresh = confirmations[witness["candidate_id"]]
                    self.assertTrue(fresh["supported"])
                    self.assertGreater(fresh["fresh_newest_at"], witness["first_newest_at"])
                    self.assertLessEqual(fresh["newest_at"], event.trace_at)
                    stamps = [
                        (stamp, witness["quantity"], witness["source"])
                        for stamp in (witness["onset_at"], fresh["fresh_newest_at"])
                    ]
                    if bound_at is not None:
                        recovery_quantity = fresh["recovery_quantity"]
                        self.assertIn(recovery_quantity, ("temperature", "humidity"))
                        recovery_witness = opening_witnesses[
                            witness["position"], recovery_quantity
                        ]
                        self.assertEqual(fresh["recovery_source"], recovery_witness["source"])
                        self.assertEqual(
                            fresh["recovery_time_basis"], recovery_witness["time_basis"],
                        )
                        self.assertEqual(
                            fresh["recovery_source_epochs"], recovery_witness["source_epochs"],
                        )
                        self.assertLessEqual(fresh["fresh_observed_at"], bound_at)
                        self.assertLessEqual(fresh["recovery_newest_at"], event.trace_at)
                        self.assertGreaterEqual(
                            fresh["recovery_start_at"],
                            event.trace_at - timedelta(seconds=parameters.values[
                                "door_window_seconds" if recovery_quantity == "temperature"
                                else "door_humidity_seconds"
                            ]),
                        )
                        stamps.extend(
                            (stamp, recovery_quantity, fresh["recovery_source"])
                            for stamp in (fresh["recovery_start_at"], fresh["recovery_newest_at"])
                        )
                    for stamp, quantity, source in stamps:
                        self.assertTrue(any(
                            item.position.value == witness["position"]
                            and item.quantity.value == quantity
                            and item.source == source
                            and item.received_at == stamp
                            for item in inputs
                        ))
                if bound_at is not None:
                    self.assertTrue(any(
                        item["recovery_observed_at"] == event.trace_at
                        or (item["recovery_quantity"] == "humidity"
                            and item["current_temperature_fresh_observed_at"] == event.trace_at)
                        for item in confirmations.values()
                    ))

    def test_later_door_closures_recover_from_original_temperature_and_humidity(self):
        origin = datetime.fromisoformat(fixture("session")["origin"])
        parameters = detection_parameters()
        values = parameters.values
        # Supply only the existing curve and ventilation baselines before each
        # opening reference, without carrying earlier detector conclusions in.
        warmup = timedelta(seconds=max(
            values["curve_window_seconds"],
            values["vent_baseline_seconds"] + max(
                values["door_window_seconds"], values["door_humidity_seconds"],
            ),
        ))

        def at(hour, minute, second=0, microsecond=0):
            return origin.replace(
                hour=hour, minute=minute, second=second, microsecond=microsecond,
            )

        # The first closure interval was independently accepted from the real
        # curves. The second is a broad assessment interval of their observed
        # fall-to-plateau recovery, not a ten-second physical contact timestamp:
        # at 15:26:00/20/40 upper T is 86.14/85.29/84.99 and lower T is
        # 78.40/77.07/76.78; RH settles before recovering. Both use raw originals.
        cases = (
            (
                "after_ventilation", at(14, 48, 40), at(14, 50, 5), at(14, 50, 20),
                at(14, 50, 20) + timedelta(seconds=values["curve_window_seconds"]),
                at(14, 50, 13, 541953), (at(14, 50, 24), at(14, 50, 45)),
            ),
            (
                "short_under_heating", at(15, 25, 54), at(15, 26), at(15, 27),
                at(15, 28), at(15, 25, 21, 10284), (at(15, 26, 50), at(15, 27)),
            ),
        )
        path = Path(__file__).parent / "fixtures/replay/R04.samples.jsonl.gz"
        for case in cases:
            name, opening_reference, earliest, latest, end, heating_at, checkpoints = case
            with self.subTest(case=name):
                started = perf_counter()
                begin = opening_reference - warmup
                observed = []
                detector = Detector(parameters, begin, observer=observed.append)
                # Completed R04 replay 38609533414 actuator history: OFF until
                # 14:50:13.541953, then ON until 15:12:15.892999; OFF until
                # 15:25:21.010284, then ON until 15:39:33.522890. Preserve each
                # switch's original timestamp.
                heating_reports = ((begin, False), (heating_at, True))
                inputs = []
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
                cursor = heating_cursor = 0
                now = begin
                while now <= end:
                    while cursor < len(inputs) and inputs[cursor].received_at <= now:
                        self.assertTrue(detector.accept(inputs[cursor]))
                        cursor += 1
                    while (
                        heating_cursor < len(heating_reports)
                        and heating_reports[heating_cursor][0] <= now
                    ):
                        reported_at, heating = heating_reports[heating_cursor]
                        detector.report_heating(heating, reported_at)
                        heating_cursor += 1
                    events.extend(detector.advance(now, enabled=True))
                    now += timedelta(seconds=detector.grid_seconds)

                diagnostic_times = {*checkpoints, latest, end}
                diagnostics = {
                    "case": name,
                    "window": (begin, end),
                    "heating_reports": heating_reports,
                    "closure_interval": (earliest, latest),
                    "events": [
                        (event.kind.value, event.effective_at,
                         event.detected_at, event.trace_at)
                        for event in events
                    ],
                    "evidence": [
                        {
                            "at": row["at"],
                            "signals": row["signals"],
                            "conditions": row["conditions"],
                            "candidates": {
                                route: {
                                    position: {
                                        quantity: {key: candidate.get(key) for key in (
                                            "status", "onset_at", "first_observed_at",
                                            "fresh_observed_at", "slope_before",
                                            "slope_after", "score_gain", "after_trend",
                                            "recovery_trend",
                                        )}
                                        for quantity, candidate in quantities.items()
                                    }
                                    for position, quantities in positions.items()
                                }
                                for route, positions in row["curve_candidates"].items()
                                if route in ("door_open", "door_open_heating", "door_close")
                            },
                            "proposals": {
                                route: [
                                    {key: proposal.get(key) for key in (
                                        "status", "onset_at", "onsets", "quantities",
                                        "first_observed_at", "evidence_bound_at",
                                        "confirmed_at", "consumed_onset_at", "reason",
                                    )}
                                    for proposal in proposals
                                ]
                                for route, proposals in row["curve_proposals"].items()
                                if route in ("door_open", "door_open_heating", "door_close")
                            },
                        }
                        for row in observed
                        if row["at"] in diagnostic_times or row["signals"]
                    ],
                }
                diagnostic = repr(diagnostics)
                print(
                    f"R04 {name}: samples={cursor} ticks={len(observed)} "
                    f"wall_seconds={perf_counter() - started:.6f} "
                    f"events={diagnostics['events']!r}",
                )
                doors = [event for event in events
                         if event.kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE)]
                self.assertEqual(
                    [event.kind for event in doors],
                    [Kind.DOOR_OPEN, Kind.DOOR_CLOSE], msg=diagnostic,
                )
                opening, close = doors
                opening_row = next(
                    item for item in observed if item["at"] == opening.trace_at
                )
                opening_packages = [
                    proposal for route in ("door_open", "door_open_heating")
                    for proposal in opening_row["curve_proposals"].get(route, ())
                    if proposal["status"] == "consumed"
                ]
                self.assertEqual(len(opening_packages), 1, msg=diagnostic)
                opening_witnesses = {
                    (witness["position"], witness["quantity"]): witness
                    for witness in opening_packages[0]["witnesses"]
                }
                self.assertLess(opening.effective_at, close.effective_at, msg=diagnostic)
                self.assertLess(opening.trace_at, close.trace_at, msg=diagnostic)
                self.assertGreaterEqual(close.effective_at, earliest, msg=diagnostic)
                self.assertLessEqual(close.effective_at, latest, msg=diagnostic)
                self.assertLessEqual(close.trace_at, end, msg=diagnostic)
                for event in doors:
                    self.assertGreater(event.trace_at, event.effective_at, msg=diagnostic)
                    self.assertEqual(event.detected_at, event.trace_at, msg=diagnostic)
                    self.assertIn(
                        event.effective_at, {item.received_at for item in inputs},
                        msg=diagnostic,
                    )
                row = next(item for item in observed if item["at"] == close.trace_at)
                consumed = [
                    proposal for proposal in row["curve_proposals"].get("door_close", ())
                    if proposal["status"] == "consumed"
                ]
                self.assertEqual(len(consumed), 1, msg=diagnostic)
                package = consumed[0]
                self.assertEqual(
                    package["consumed_onset_at"], close.effective_at, msg=diagnostic,
                )
                self.assertLess(package["first_observed_at"], close.trace_at, msg=diagnostic)
                self.assertEqual(package["confirmed_at"], close.trace_at, msg=diagnostic)
                confirmations = {
                    item["candidate_id"]: item for item in package["confirmation"]
                }
                for witness in package["witnesses"]:
                    fresh = confirmations[witness["candidate_id"]]
                    self.assertTrue(fresh["supported"], msg=diagnostic)
                    self.assertGreater(
                        fresh["fresh_newest_at"], witness["first_newest_at"],
                        msg=diagnostic,
                    )
                    self.assertLessEqual(fresh["newest_at"], close.trace_at, msg=diagnostic)
                    for stamp in (witness["onset_at"], fresh["fresh_newest_at"]):
                        self.assertTrue(any(
                            item.position.value == witness["position"]
                            and item.quantity.value == witness["quantity"]
                            and item.source == witness["source"]
                            and item.received_at == stamp
                            for item in inputs
                        ), msg=diagnostic)
                    if package.get("evidence_bound_at") is None:
                        continue
                    recovery_quantity = fresh["recovery_quantity"]
                    self.assertIn(recovery_quantity, ("temperature", "humidity"))
                    recovery_witness = opening_witnesses[
                        witness["position"], recovery_quantity
                    ]
                    for key in ("source", "time_basis", "source_epochs"):
                        self.assertEqual(
                            fresh[f"recovery_{key}"], recovery_witness[key],
                            msg=diagnostic,
                        )
                    window = timedelta(seconds=values[
                        "door_window_seconds" if recovery_quantity == "temperature"
                        else "door_humidity_seconds"
                    ])
                    self.assertGreaterEqual(
                        fresh["recovery_start_at"],
                        max(witness["onset_at"], close.trace_at - window),
                        msg=diagnostic,
                    )
                    self.assertLessEqual(
                        fresh["recovery_start_at"], fresh["recovery_newest_at"],
                        msg=diagnostic,
                    )
                    self.assertLessEqual(fresh["recovery_newest_at"], close.trace_at, msg=diagnostic)
                    self.assertLessEqual(fresh["recovery_observed_at"], close.trace_at, msg=diagnostic)
                    recovery_stamps = [fresh["recovery_start_at"], fresh["recovery_newest_at"]]
                    for stamp in recovery_stamps:
                        self.assertTrue(any(
                            item.position.value == witness["position"]
                            and item.quantity.value == recovery_quantity
                            and item.source == fresh["recovery_source"]
                            and item.received_at == stamp
                            for item in inputs
                        ), msg=diagnostic)
                    if recovery_quantity == "humidity":
                        self.assertTrue(fresh["current_temperature_supported"], msg=diagnostic)
                        newest = fresh["current_temperature_newest_at"]
                        self.assertGreaterEqual(
                            newest, close.trace_at - timedelta(seconds=values["door_window_seconds"]),
                            msg=diagnostic,
                        )
                        self.assertLessEqual(newest, close.trace_at, msg=diagnostic)
                        self.assertTrue(any(
                            item.position.value == witness["position"]
                            and item.quantity == Quantity.TEMPERATURE
                            and item.source == witness["source"]
                            and item.received_at == newest
                            for item in inputs
                        ), msg=diagnostic)
                if package.get("evidence_bound_at") is not None:
                    self.assertTrue(any(
                        item["recovery_observed_at"] == close.trace_at
                        or (item["recovery_quantity"] == "humidity"
                            and item["current_temperature_fresh_observed_at"] == close.trace_at)
                        for item in confirmations.values()
                    ), msg=diagnostic)
