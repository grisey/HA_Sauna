"""B4e Details · Kalibrierung: Läufe und Ergebnis je Schritt aus dem Archiv.

L-B4-3: nach einem Lauf je Zelle gefunden im Fenster, außerhalb zugeordnet mit
Lage, fehlt, zusätzlich. L-W2c-1: das letzte Ergebnis kommt aus dem Archiv und
steht damit auch nach einem Neuladen. L-W2c-3: der Abbruch aus dem Panel nennt
den Lauf. Bewertung und Zuordnung (``calibration.evaluate``) bleiben unverändert;
die Fälle lesen ihr Ergebnis nur.
"""

import asyncio
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

import test_calibration as base
from test_calibration import A1, CB, USER, D, at

from custom_components.ha_sauna.archive import plain
from custom_components.ha_sauna.core import calibration, calibration_runs
from custom_components.ha_sauna.core.notification_recipients import ActorRef


def protocol(module, windows, *, run_id="run", execution=1, outcomes=None):
    steps = calibration.MODULES[module].steps
    return [{"event": "step", "run_id": run_id, "execution": execution, "module": module,
             "step": step.key, "reference": step.reference, "handoff_at": at(a), "closed_at": at(b),
             "outcome": (outcomes or {}).get(step.key, "reply" if step.action else "hold")}
            for step, (a, b) in zip(steps, windows, strict=False)]


def records(run_id, kind, modules, steps, *, end=None, events=(), has_presence=False):
    """Archived protocol of one run as the archive returns it (JSON)."""
    items = [{"event": "start", "run_id": run_id, "at": at(0), "kind": kind,
              "modules": modules, "user_id": USER}]
    numbers = {}
    for step in steps:
        if step["execution"] not in numbers:
            numbers[step["execution"]] = step["module"]
            items.append({"event": "module", "run_id": run_id, "at": step["handoff_at"],
                          "execution": step["execution"], "module": step["module"]})
        items.append({**step, "at": step["closed_at"]})
    if end is not None:
        items.append({"event": "end", "run_id": run_id, "at": steps[-1]["closed_at"],
                      "reason": end, "valid": end == "finished", "user_id": None})
    if end == "finished" and events is not None:
        result = calibration.evaluate(steps, presence=[], events=list(events), gangs=[],
                                      gang_heat=[], until=at(3600), has_presence=has_presence)
        items.append({"event": "evaluation", "run_id": run_id, "at": at(3600), "result": result})
    return json.loads(json.dumps(plain(items)))


def processed(session_id, events):
    return [{"event_id": f"recognition:{session_id}:{kind}:{moment.isoformat()}",
             "kind": kind, "effective_at": moment.isoformat()} for kind, moment in events]


class CalibrationRunResultTests(unittest.TestCase):
    def test_every_cell_shows_found_outside_with_its_offset_missing_and_additional(self):
        # A1 as in the evaluation tests: infusion 5 s after its reply, an extra
        # opening before the exit, the exit closing after the last window.
        events = [("door_open", at(5)), ("door_close", at(15)), ("infusion", at(395)),
                  ("door_open", at(400)), ("door_open", at(428)), ("door_close", at(500))]
        items = records("run", "module", ["A1"], protocol("A1", A1), end="finished",
                        events=events)
        result = calibration_runs.result("run", "s", items, processed=processed("s", events))
        self.assertEqual((result["kind_text"], result["modules"], result["valid"],
                          result["evaluated"]), ("Einzelnes Modul", ["A1"], True, True))
        self.assertEqual(result["totals"]["events"]["found"], 3)
        cells = result["executions"][0]["cells"]
        self.assertEqual([(c["type"], c.get("reference", c.get("label")), c["status"])
                          for c in cells], [
            ("step", "Eintritt", "found"),
            ("step", "Aufguss", "outside"),
            ("additional", "Tür auf", "additional"),
            ("step", "Austrittsöffnung = Gangende", "found"),
            ("step", "Austrittsschluss", "missing"),
        ])
        outside = cells[1]["expected"][0]
        self.assertEqual((outside["status_text"], outside["offset_s"], outside["event_id"]),
                         ("außerhalb des Fensters", 5.0, f"recognition:s:infusion:{at(395).isoformat()}"))
        self.assertEqual(cells[2]["event_id"], f"recognition:s:door_open:{at(400).isoformat()}")
        self.assertEqual(cells[2]["window"], [at(400).isoformat()] * 2)
        self.assertEqual(cells[4]["expected"][0]["event_id"], None)
        self.assertEqual(cells[0]["window"], [at(0).isoformat(), at(20).isoformat()])
        self.assertEqual(result["additional"], [])
        # Without a presence sensor the run measured no presence.
        self.assertIs(result["presence_measured"], False)

    def test_a_cell_with_different_outcomes_and_merged_door_operations(self):
        # C/B: the closing of the short opening is missed; the recognition merged it
        # with the opening in the decay (W-2b).
        events = [("door_open", at(5)), ("door_close", at(15)), ("door_open", at(82)),
                  ("door_close", at(88)), ("door_open", at(272)), ("door_close", at(485)),
                  ("infusion", at(405)), ("door_open", at(612)), ("door_close", at(685))]
        items = records("run", "module", ["CB"], protocol("CB", CB), end="finished",
                        events=events)
        execution = calibration_runs.result("run", "s", items)["executions"][0]
        statuses = {cell["step"]: cell["status"] for cell in execution["cells"]}
        self.assertEqual(statuses["short_opening"], "mixed")
        self.assertEqual(statuses["decay_opening"], "mixed")
        self.assertEqual(execution["merged"], [{
            "opening": {"reference": "kurze Öffnung vor dem Aufguss", "module": "C/B",
                        "at": at(272).isoformat()},
            "closing": {"reference": "kurze Öffnung im Abklingen", "module": "C/B",
                        "at": at(485).isoformat()},
        }])

    def test_deviating_steps_and_steps_without_expectation(self):
        # D: the second pour has no expectation of its own; the second infusion
        # was marked deviating and stays without reference.
        steps = protocol("D", D, outcomes={"infusion_2": "deviate"})
        events = [("door_open", at(5)), ("door_close", at(15)), ("infusion", at(205)),
                  ("door_open", at(605)), ("door_close", at(675))]
        result = calibration_runs.result("run", "s", records(
            "run", "module", ["D"], steps, end="finished", events=events))
        statuses = [(cell["step"], cell["status"], cell["status_text"])
                    for cell in result["executions"][0]["cells"]]
        self.assertIn(("infusion_1_second", "none", "keine Erkennung erwartet"), statuses)
        self.assertIn(("infusion_2", "excluded", "abweichend, ohne Referenz"), statuses)
        self.assertEqual(result["totals"]["excluded_steps"], 1)

    def test_invalid_runs_steps_not_reached_and_a_protocol_without_end(self):
        # A calibration session cancelled in the infusion step of A1: the open step
        # is recorded, the following ones and module D are not reached.
        steps = protocol("A1", A1[:2], outcomes={"infusion": "cancelled"})
        result = calibration_runs.result("run", "s", records(
            "run", "session", ["A1", "D"], steps, end="cancelled"))
        self.assertEqual((result["valid"], result["reason_text"], result["evaluated"]),
                         (False, "abgebrochen", False))
        first, second = result["executions"]
        self.assertEqual([cell["status"] for cell in first["cells"]],
                         ["unevaluated", "cancelled", "not_reached", "not_reached"])
        self.assertEqual((second["execution"], second["label"]), (None, "D"))
        self.assertTrue(all(cell["status"] == "not_reached" and cell["window"] is None
                            for cell in second["cells"]))
        # Without an end record (an unclean stop) the run is invalid as well.
        summary = calibration_runs.summary("run", "s", records(
            "run", "module", ["A1"], protocol("A1", A1[:1])))
        self.assertEqual((summary["valid"], summary["reason"], summary["reason_text"]),
                         (False, None, "Protokoll ohne Ende"))

    def test_a_run_of_a_changed_procedure_keeps_its_protocol_steps(self):
        # The procedure may change; an archived run then still shows its closed
        # steps with the expectations of its evaluation, independent of its gangs.
        events = [("door_open", at(5)), ("door_close", at(15)), ("infusion", at(385))]
        items = records("run", "session", ["A1", "B9"], protocol("A1", A1), end="finished",
                        events=events)
        for item in items:
            if item.get("module") == "A1":
                item["module"] = "X1"
            if item.get("event") == "start":
                item["modules"] = ["X1", "B9"]
            for execution in (item.get("result") or {}).get("executions") or ():
                execution["module"] = "X1"
        result = calibration_runs.result("run", "s", items)
        first, second = result["executions"]
        self.assertEqual((first["label"], first["title"]), ("X1", ""))
        self.assertEqual([(cell["reference"], cell["expects"], cell["status"])
                          for cell in first["cells"]], [
            ("Eintritt", ["Tür auf", "Tür zu"], "found"),
            ("Aufguss", ["Aufguss"], "found"),
            ("Austrittsöffnung = Gangende", ["Tür auf"], "missing"),
            ("Austrittsschluss", ["Tür zu"], "missing"),
        ])
        self.assertEqual([(cell["reference"], cell["status"]) for cell in second["cells"]],
                         [("nicht begonnen", "not_reached")])
        self.assertEqual(result["modules"], ["X1", "B9"])

    def test_a_valid_run_waits_for_the_evaluation_after_the_session_end(self):
        items = records("run", "module", ["A1"], protocol("A1", A1), end="finished", events=None)
        result = calibration_runs.result("run", "s", items)
        self.assertEqual((result["valid"], result["evaluated"], result["totals"]),
                         (True, False, None))
        self.assertEqual({cell["status"] for cell in result["executions"][0]["cells"]},
                         {"pending"})

    def test_runs_are_listed_latest_first_without_the_active_one(self):
        earlier = records("r1", "module", ["A1"], protocol("A1", A1, run_id="r1"),
                          end="cancelled")
        later = [{**item, "at": item["at"].replace("2026-11-02", "2026-11-03")}
                 for item in records("r2", "module", ["D"], protocol("D", D, run_id="r2"),
                                     end="finished", events=[])]
        active = records("r3", "module", ["CB"], protocol("CB", CB[:1], run_id="r3"))
        stored = [("s1", item) for item in earlier] + [("s2", item) for item in later] + [
            ("s2", item) for item in active]
        view = calibration_runs.view(stored, active_run_id="r3")
        self.assertEqual([run["run_id"] for run in view["runs"]], ["r2", "r1"])
        self.assertEqual(view["run"]["run_id"], "r2")
        self.assertEqual(calibration_runs.view(stored, run_id="r1", active_run_id="r3")["run"][
            "reason_text"], "abgebrochen")
        self.assertIsNone(calibration_runs.view(stored, run_id="r3", active_run_id="r3"))
        self.assertEqual(calibration_runs.view([]), {"runs": [], "run": None})


class CalibrationRunArchiveTests(unittest.TestCase):
    """Productive runtime and archive: a module run, its session end and a reload."""

    def test_the_last_result_comes_from_the_archive_and_survives_a_reload(self):
        helper = base.CalibrationRuntimeTests("test_start_conditions")

        async def exercise(directory):
            clock = [base.START]
            runtime = helper.runtime(clock)
            await runtime.start_archive(Path(directory) / "a.sqlite", "entry")
            runtime.controller.set_temperature(80, base.START)
            await runtime.set_operation(True, actor=ActorRef(USER, "ha_context"))
            await helper.sensor_gang(runtime, clock, calibrate=True)
            await helper.settle(runtime)
            run = runtime.calibration_runs[0]
            status = runtime.calibration_status["active"]
            self.assertEqual(status["run_id"], run.run_id)
            # L-W2c-3: a stale run_id never cancels the running run.
            with self.assertRaisesRegex(ValueError, "läuft nicht mehr"):
                await runtime.cancel_calibration(actor=ActorRef(USER, "x"), run_id="other")
            self.assertTrue(run.active)
            await runtime.archive.flush()
            # The running run is not listed; its status shows it.
            self.assertEqual(runtime.archive.calibration_runs(active_run_id=run.run_id),
                             {"runs": [], "run": None})
            first = runtime.notification_transport.sent[0]
            clock[0] += timedelta(seconds=10)
            await runtime.calibration_reply(helper.association(first, "reply", clock[0]))
            await helper.complete(runtime, clock)
            await runtime.archive.flush()
            pending = runtime.archive.calibration_runs()["run"]
            self.assertEqual((pending["valid"], pending["evaluated"]), (True, False))
            await helper.end_session(runtime, clock)
            await runtime.archive.flush()
            path = runtime.archive.path
            session_id = run.session_id
            await runtime.close()
            # A fresh archive owner (after a reload) reads the same result.
            from custom_components.ha_sauna.archive import Archive
            from custom_components.ha_sauna.core.parameters import Parameters

            reloaded = Archive(path, "entry", parameters=Parameters({}))
            view = reloaded.calibration_runs()
            self.assertEqual([item["run_id"] for item in view["runs"]], [run.run_id])
            result = view["run"]
            self.assertEqual((result["session_id"], result["valid"], result["evaluated"]),
                             (session_id, True, True))
            self.assertEqual(result["totals"], runtime.calibration_status["last"]["totals"])
            self.assertEqual([cell["reference"] for cell in result["executions"][0]["cells"]],
                             [step.reference for step in calibration.MODULES["A1"].steps])
            self.assertIsNone(reloaded.calibration_runs("unknown"))

        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(exercise(directory))

    def test_cancel_with_the_run_id_cancels_that_run(self):
        helper = base.CalibrationRuntimeTests("test_start_conditions")

        async def exercise():
            clock = [base.START]
            runtime, run = await helper.module_in_session(clock)
            result = await runtime.cancel_calibration(actor=ActorRef(USER, "x"),
                                                      run_id=run.run_id)
            self.assertEqual(result, {"run_id": run.run_id})
            self.assertEqual((run.phase, run.end_reason), ("invalid", "cancelled"))
            with self.assertRaisesRegex(ValueError, "keine Kalibrierung"):
                await runtime.cancel_calibration(run_id=run.run_id)
            await runtime.close()

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
