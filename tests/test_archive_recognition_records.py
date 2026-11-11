"""B2: Umgebung, verworfene Kandidaten und Zustandsverlauf im normalen Archivprofil.

Die Erkennung bleibt unverändert; die Einträge lesen nur ihre Diagnose je
Messschritt, ihr Entscheidungsprotokoll und die gemeldeten Episoden. Die
Umgebung wird zum Sitzungsbeginn und bei jeder Änderung der gebundenen Quelle
gespeichert, ohne eigenen Takt und ohne Bindung gar nicht.
"""

import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import numpy as np
import test_recognition_model as model
from test_foundation import T0, bindings, parameters
from test_presence_regressions import _runtime

from custom_components.ha_sauna.bindings import Bindings
from custom_components.ha_sauna.core.presence import binary_presence
from custom_components.ha_sauna.core.recognition import (
    ClimateRecognizer,
    RejectedOpening,
)
from custom_components.ha_sauna.core.recognition_archive import RecognitionArchive
from custom_components.ha_sauna.core.recognition_model import CLOSED, NM, OPEN, RECOVERY
from custom_components.ha_sauna.core.timeline import DoorCloseEvidence
from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime

STATES = ("closed", "open", "infusion", "decay", "recovery", "shift")
NEW_KINDS = ("recognition_state", "recognition_rejected", "recognition_episode", "environment")


def t(second):
    return T0 + timedelta(seconds=second)


def trace(second, **probabilities):
    return {"at": t(second).isoformat(),
            "state_probabilities": {**dict.fromkeys(STATES, 0.0), **probabilities}}


async def stored(runtime, session_id, kinds=NEW_KINDS + ("detector_trace", "recognition_decay")):
    runtime.persist()
    await runtime.archive.flush()
    records = runtime.archive.read(session_id, limit=10000, now=runtime._clock())["records"]
    return [(item["kind"], item["payload"]) for item in records if item["kind"] in kinds]


class RecognitionRecordTests(unittest.IsolatedAsyncioTestCase):
    async def records(self, profile, traces, decisions=()):
        """Archived records after the observer saw ``traces``; ``decisions``: (step, decision)."""
        with tempfile.TemporaryDirectory() as directory:
            runtime = SaunaRuntime(Configuration(bindings(), parameters()), lambda: T0)
            await runtime.start_archive(Path(directory) / "a.sqlite", "entry", trace_profile=profile)
            try:
                runtime.controller.begin_session("s", T0)
                runtime._sync_detector()
                for index, item in enumerate(traces):
                    runtime.detector.decisions.extend(d for step, d in decisions if step == index)
                    runtime.detector.observer(item)
                return await stored(runtime, "s")
            finally:
                await runtime.archive.close()

    async def test_state_changes_in_the_normal_profile_without_step_records(self):
        traces = [
            trace(1, closed=.9, recovery=.1),
            trace(2, closed=.8, recovery=.2),
            trace(3, closed=.45, open=.45, recovery=.1),     # a tie keeps the state
            trace(4, open=.7, closed=.3),
            trace(5, open=.6, closed=.4),
            trace(6, recovery=.5, open=.3, closed=.2),
        ]
        changes = [("recognition_state", {"at": t(s).isoformat(), "state": state, "probability": p})
                   for s, state, p in ((1, "closed", .9), (4, "open", .7), (6, "recovery", .5))]
        self.assertEqual(await self.records("normal", traces), changes)
        analysis = await self.records("analysis", traces)
        self.assertEqual([item for item in analysis if item[0] == "recognition_state"], changes)
        self.assertEqual([item[1] for item in analysis if item[0] == "detector_trace"], traces)

    async def test_rejected_candidate_with_reason_and_ratio_of_its_state(self):
        decisions = [
            (0, {"event": "door_open", "effective_at": t(10).isoformat(),
                 "decided_at": t(12).isoformat(), "state": "open", "against": "recovery",
                 "ratio": 293.2, "steps_to_decision": 2, "difference_factor": 0.93}),
            (1, {"event": "door_close", "effective_at": t(14).isoformat(),
                 "decided_at": t(16).isoformat(), "state": "recovery", "against": "open",
                 "ratio": 2451.4, "steps_to_decision": 0, "difference_factor": 1.08}),
            (1, {"event": "door_open_rejected", "effective_at": t(10).isoformat(),
                 "decided_at": t(16).isoformat(), "reason": "opening_form"}),
        ]
        records = await self.records("normal", [trace(12, open=.9), trace(16, recovery=.9),
                                                trace(17, recovery=.9)], decisions)
        self.assertEqual([item for item in records if item[0] == "recognition_rejected"], [
            ("recognition_rejected", {
                "kind": "door_open", "effective_at": t(10).isoformat(), "decided_at": t(16).isoformat(),
                "reason": "opening_form", "state": "open", "against": "recovery", "ratio": 293.2,
            }),
        ])

    async def test_flattened_decay_decision_is_archived_once(self):
        # AF: die vorhandene Entscheidung „Abklingen abgeflacht“ wird archiviert, unverändert.
        flattened = {"event": "infusion_decay_flattened", "effective_at": t(70).isoformat(),
                     "decided_at": t(70).isoformat(), "steepest_at": t(40).isoformat(),
                     "slope": -0.00012, "steepest": -0.00051, "ratio": 120.3}
        decisions = [
            (0, {"event": "infusion_pour", "effective_at": t(30).isoformat(),
                 "decided_at": t(32).isoformat(), "joined": "…"}),
            (1, flattened),
        ]
        records = await self.records("normal", [trace(32, infusion=.9), trace(70, decay=.9),
                                                trace(71, decay=.9)], decisions)
        self.assertEqual([item for item in records if item[0] == "recognition_decay"], [
            ("recognition_decay", {"effective_at": t(70).isoformat(), "decided_at": t(70).isoformat(),
                                   "steepest_at": t(40).isoformat()}),
        ])

    def test_rejected_candidates_from_the_decisions_of_the_recognizer(self):
        # the rejection of test_recognition_model: an opening the form does not show
        params = model.parameters()
        params["open_form"] = {"rates": [[0.5, 1 / 300], [0.5, 1 / 300], None, None]}
        recognizer = ClimateRecognizer(params, model.T0)
        n = 30
        steps = np.arange(float(n))
        recognizer._grid_t = list(steps)
        recognizer._grid_y = [np.array([80 + 0.01 * v, 72 + 0.01 * v, 30.0, 28.0]) for v in steps]

        def row(state):
            r = np.full(NM, 1e-4)
            r[state] = 1.0
            return r / r.sum()

        archive = RecognitionArchive()
        rows = [row(CLOSED)] * 10 + [row(OPEN)] * 8 + [row(RECOVERY)] * 12
        found = []
        for k in range(n):
            recognizer._steps = k + 1
            recognizer._post, recognizer._post_base = np.array(rows[:k + 1]), 0
            recognizer._decide_door(model.T0 + timedelta(seconds=float(k)))
            found.extend(archive.rejected_candidates(recognizer.decisions))
        candidate = next(d for d in recognizer.decisions if d["event"] == "door_open")
        rejection = next(d for d in recognizer.decisions if d["event"] == "door_open_rejected")
        self.assertEqual(found, [{
            "kind": "door_open", "effective_at": rejection["effective_at"],
            "decided_at": rejection["decided_at"], "reason": "opening_form",
            "state": "open", "against": candidate["against"], "ratio": candidate["ratio"],
        }])
        self.assertEqual(archive.rejected_candidates(recognizer.decisions), [])

    async def test_episode_with_its_result(self):
        async def exercise(directory):
            clock = [T0]
            configuration = _runtime(clock).configuration
            configuration = replace(configuration, presence_source="ha_presence",
                                    bindings=Bindings({**configuration.bindings.values,
                                                       "presence": "binary_sensor.presence"}))
            runtime = SaunaRuntime(configuration, clock=lambda: clock[0])
            await runtime.start_archive(Path(directory) / "e.sqlite", "entry")
            try:
                runtime.controller.set_temperature(70, T0)
                await runtime.set_operation(True)
                sid = runtime.session.session_id

                async def presence(value, second):
                    clock[0] = t(second)
                    await runtime.accept_presence(binary_presence(
                        "binary_sensor.presence", value, clock[0], clock[0]))

                await presence("off", 1)
                await presence("on", 12)
                channels = ("Tu", "Tl", "Au", "Al")
                door = RejectedOpening(t(10), t(20), t(25), t(45), t(46), (t(10),), channels,
                                       DoorCloseEvidence(t(20), t(45)))
                # within the gang the door opened, an episode stays rejected (G9-4)
                rejected = RejectedOpening(t(60), t(64), t(66), t(86), t(87), (t(60), t(63)), channels,
                                           DoorCloseEvidence(t(64), t(86)))
                pending = [(t(46), door), (t(87), rejected)]

                def advance(through, **callbacks):
                    while pending and through >= pending[0][0]:
                        callbacks["on_rejected_opening"](pending.pop(0)[1])
                    return []

                with patch.object(runtime.detector, "advance", side_effect=advance):
                    for second in (47, 88):
                        clock[0] = t(second)
                        runtime._deliver_detection(clock[0])
                records = await stored(runtime, sid, ("recognition_episode",))
                origins = [item["payload"].get("origin") for item in
                           runtime.archive.read(sid, limit=10000, now=clock[0])["records"]
                           if item["kind"] == "detection"]
            finally:
                await runtime.archive.close()
            return records, origins

        with tempfile.TemporaryDirectory() as directory:
            records, origins = await exercise(directory)
        self.assertEqual([payload for _, payload in records], [
            {"opened_at": t(10).isoformat(), "closed_at": t(20).isoformat(),
             "close_candidate_at": t(25).isoformat(), "ended_at": t(45).isoformat(),
             "decided_at": t(46).isoformat(), "candidates": [t(10).isoformat()], "result": "door"},
            {"opened_at": t(60).isoformat(), "closed_at": t(64).isoformat(),
             "close_candidate_at": t(66).isoformat(), "ended_at": t(86).isoformat(),
             "decided_at": t(87).isoformat(), "candidates": [t(60).isoformat(), t(63).isoformat()],
             "result": "rejected"},
        ])
        # the door operation itself stays the booked detections with origin presence
        self.assertEqual(origins, ["presence", "presence"])


def snapshot(temperature, updated, condition="cloudy", measured=None):
    return {
        "configured": True, "station": {"name": "Station", "id": "S1"}, "condition": condition,
        "values": [{"key": "temperature", "label": "Temperatur", "state": str(temperature),
                    "value": temperature, "unit": "°C", "available": True,
                    "last_updated": updated.isoformat(), "entity_id": "sensor.outside"}],
        "measurement_time": measured.isoformat() if measured is not None else None,
        "forecast_time": None, "source": {"mode": None, "interpolated": None},
        "updated_at": updated.isoformat(),
        "entities": {"weather": None, "measurement_time": "sensor.measured", "forecast_time": None},
    }


class EnvironmentRecordTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.clock = [T0]
        self.runtime = SaunaRuntime(Configuration(bindings(), parameters()), lambda: self.clock[0])
        await self.runtime.start_archive(Path(self.directory.name) / "u.sqlite", "entry")

    async def asyncTearDown(self):
        await self.runtime.archive.close()
        self.directory.cleanup()

    def start(self):
        self.runtime.controller.begin_session("s", self.clock[0])
        self.runtime._sync_detector()

    async def environment(self):
        return [payload for _, payload in await stored(self.runtime, "s", ("environment",))]

    def report(self, second, *args, **kwargs):
        self.clock[0] = t(second)
        self.current[0] = snapshot(*args, **kwargs)
        self.runtime.environment_changed()

    async def test_at_the_session_start_and_for_each_new_measurement_time(self):
        self.current = [snapshot(4.2, t(-1200), measured=t(-1800))]
        self.runtime.environment = lambda: self.current[0]
        self.runtime.environment_changed()               # no session: nothing
        self.start()
        # interpolated values and HA updates within the same measurement: nothing new
        self.report(600, 4.1, t(600), condition="rainy", measured=t(-1800))
        self.report(1200, 4.0, t(1200), measured=t(-1800))
        self.report(1800, 3.9, t(1800), measured=t(1800))
        self.report(2400, 3.9, t(2400), measured=t(1800))
        records = await self.environment()
        self.assertEqual([(r["reason"], r["values"][0]["value"], r["measurement_time"])
                          for r in records], [
            ("session_start", 4.2, t(-1800).isoformat()),
            ("source_changed", 3.9, t(1800).isoformat()),
        ])
        self.assertNotIn("label", records[0]["values"][0])
        self.assertNotIn("configured", records[0])
        self.assertEqual(records[0]["station"], {"name": "Station", "id": "S1"})
        # the record names the supplying entities
        self.assertEqual(records[0]["values"][0]["entity_id"], "sensor.outside")
        self.assertEqual(records[0]["entities"]["measurement_time"], "sensor.measured")

    async def test_the_start_entry_is_written_even_after_an_earlier_report(self):
        # K-S20 is the value of the start entry; a report just before it does not replace it
        self.current = [snapshot(4.2, t(-1200), measured=t(-1800))]
        self.runtime.environment = lambda: self.current[0]
        self.runtime.controller.begin_session("s", self.clock[0])
        self.runtime.environment_changed()
        self.runtime._sync_detector()
        self.assertEqual([r["reason"] for r in await self.environment()],
                         ["source_changed", "session_start"])

    async def test_without_measurement_time_for_each_changed_value(self):
        self.current = [snapshot(4.2, t(-1200))]
        self.runtime.environment = lambda: self.current[0]
        self.start()
        self.report(600, 4.2, t(600))                    # only the HA timestamp: nothing
        self.report(900, 4.2, t(900), condition="rainy")  # the condition is no value
        self.report(1200, 3.9, t(1200), condition="rainy")
        self.report(1500, 3.9, t(1500), condition="rainy")
        records = await self.environment()
        self.assertEqual([(r["reason"], r["values"][0]["value"], r["values"][0]["last_updated"])
                          for r in records], [
            ("session_start", 4.2, t(-1200).isoformat()),
            ("source_changed", 3.9, t(1200).isoformat()),
        ])

    async def test_nothing_without_a_bound_source(self):
        self.start()
        self.runtime.environment_changed()
        # a snapshot without a configured binding is not archived either
        self.runtime.environment = lambda: {"configured": False, "values": []}
        self.runtime.environment_changed()
        self.assertEqual(await self.environment(), [])

    async def test_a_failing_source_does_not_disturb_the_session(self):
        def broken():
            raise RuntimeError("weather")

        self.runtime.environment = broken
        with patch.object(self.runtime.log, "error") as error:
            self.start()
        error.assert_called_once()
        self.assertEqual(error.call_args.args[0], "environment")
        self.assertIsNotNone(self.runtime.detector)
        self.assertEqual(await self.environment(), [])


if __name__ == "__main__":
    unittest.main()
