"""Calibration runs: instructions, replies, protocol and evaluation (V31, W-2, W-2a, W-2b).

The display state for the panel (W-2c) names module, step and waiting reason
of the running run and keeps the last result after its session ended.

The run never operates the sauna. Runtime cases use the productive service
methods with a synthetic final transport and compare control and gang results
with the same inputs without a calibration run. Every interruption makes a run
invalid; only uninterrupted runs are evaluated.
"""

import asyncio
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from test_presence_regressions import START, _runtime
from test_program_configuration import _FakeHass

from custom_components.ha_sauna.archive import encoded
from custom_components.ha_sauna.bindings import Bindings
from custom_components.ha_sauna.core import calibration
from custom_components.ha_sauna.core.notification_exchange import (
    ReplyAssociation,
    ReplyDisposition,
    ReplyEnvelope,
    ReplyKey,
    TransportHandoff,
)
from custom_components.ha_sauna.core.notification_recipients import (
    ActorRef,
    CapabilityEvidence,
    RecipientAssignment,
    RecipientCapability,
    RecipientRef,
    RecipientResolution,
    ResolvedRecipient,
    TargetIdentity,
)
from custom_components.ha_sauna.core.presence import binary_presence
from custom_components.ha_sauna.core.timeline import DoorCloseEvidence, Event, Kind
from custom_components.ha_sauna.runtime import SaunaRuntime
from custom_components.ha_sauna.settings import async_set_control_mode

T0 = datetime(2026, 11, 2, 12, tzinfo=UTC)
USER = "synthetic-user"
# Synthetic windows (seconds from T0) per step of a module.
A1 = ((0, 20), (380, 390), (420, 430), (475, 480))
CB = ((0, 20), (80, 90), (270, 280), (400, 410), (470, 490), (610, 620), (680, 690))
D = ((0, 20), (200, 210), (210, 225), (530, 540), (600, 610), (670, 680))


def at(seconds):
    return T0 + timedelta(seconds=seconds)


def ids(run, instruction, token):
    return {kind: item["action"] for (kind, _), item in zip(
        instruction.actions, run.native_actions(instruction, token), strict=True)}


class Driver:
    """Answers each instruction as soon as it is due, like a person following it."""

    def __init__(self, run, *, now=T0, reply_after=20):
        self.run, self.now, self.reply_after = run, now, reply_after
        self.messages = []

    def advance(self, seconds=0, *, ready=True, gang_active=False):
        self.now += timedelta(seconds=seconds)
        out = self.run.advance(self.now, ready=ready, gang_active=gang_active)
        self.messages.extend(out)
        return out

    def answer(self, instruction, kind=calibration.REPLY, *, token=None, delay=None):
        self.now += timedelta(seconds=self.reply_after if delay is None else delay)
        actions = ids(self.run, instruction, token or f"m{instruction.seq}")
        disposition, out = self.run.reply(actions[kind], self.now, USER,
                                          message_id=token or f"m{instruction.seq}")
        self.messages.extend(out)
        return disposition, out

    def follow(self):
        """Run the current module to its end; return its closing message."""
        out = self.advance()
        while True:
            if not out:
                execution = self.run.current
                if execution.sent_at is None:
                    due = (execution.due_at - self.now).total_seconds()
                    out = self.advance(max(due, 0))
                else:
                    # A step without action closes by its own time.
                    out = self.advance(execution.module.steps[execution.step].hold_seconds)
                continue
            instruction = out[-1]
            if instruction.kind != "step":
                return instruction
            out = (self.answer(instruction)[1]
                   if instruction.actions[0][0] == calibration.REPLY else [])


class CalibrationRunTests(unittest.TestCase):
    def run_for(self, kind="session", modules=None, presence=True, run_id="run"):
        selected = calibration.select_modules(modules, presence=presence)
        return calibration.CalibrationRun(run_id, kind, selected, USER, "session", T0,
                                          has_presence=presence,
                                          presence=[(T0, False)] if presence else [])

    def test_session_runs_all_modules_in_order_with_pauses(self):
        run = self.run_for()
        driver = Driver(run)
        self.assertEqual(driver.advance(ready=False), [])
        self.assertEqual(run.waiting_for, "ready")
        executed = []
        while True:
            closing = driver.follow()
            executed.append(run.current.module.module_id)
            if closing.kind == "result":
                break
            self.assertEqual(closing.kind, "pause")
            self.assertIn(calibration.NEXT, dict(closing.actions))
            self.assertEqual(driver.answer(closing, calibration.NEXT)[0], "accepted")
        self.assertEqual(executed, ["P", "A1", "A2", "A3", "CB", "D"])
        self.assertEqual((run.phase, run.valid), ("finished", True))
        end = [record for record in run.records if record["event"] == "end"]
        self.assertEqual([(r["reason"], r["valid"]) for r in end], [("finished", True)])
        self.assertIn("Auswertung der Erkennung folgt nach Sitzungsende", closing.message)
        self.assertEqual(
            [step["step"] for step in run.steps if step["module"] == "D"],
            [step.key for step in calibration.MODULES["D"].steps])
        self.assertTrue(all(step["outcome"] in ("reply", "hold") for step in run.steps))
        # "+6 min" counts from the preceding reply.
        a1 = [step for step in run.steps if step["module"] == "A1"]
        self.assertEqual(a1[1]["handoff_at"] - a1[0]["closed_at"], timedelta(minutes=6))
        # Each message offers Abweichend and Abbrechen.
        steps = [m for m in driver.messages if m.kind == "step"]
        self.assertTrue(all({calibration.DEVIATE, calibration.CANCEL} <= dict(m.actions).keys()
                            for m in steps))

    def test_cancel_makes_the_run_invalid_without_further_instruction(self):
        run = self.run_for(modules=("A1",))
        driver = Driver(run)
        entry = driver.advance()[0]
        driver.answer(entry)
        infusion = driver.advance(360)[0]
        # The service cancels outside any message; only the notice of invalidity follows.
        notice = run.interrupt(driver.now, "cancelled", user_id=USER)
        self.assertEqual([(m.kind, m.actions, m.message) for m in notice], [(
            "invalid", (), "Kalibrierung ungültig: abgebrochen. Sie wird nicht ausgewertet.")])
        self.assertEqual(run.interrupt(driver.now, "cancelled"), [])
        self.assertEqual((run.phase, run.valid), ("invalid", False))
        self.assertEqual([s["outcome"] for s in run.steps], ["reply", "cancelled"])
        self.assertEqual(driver.answer(infusion)[0], "late")
        self.assertEqual(driver.answer(infusion, calibration.CANCEL)[0], "late")
        self.assertEqual(driver.answer(entry)[0], "duplicate")
        self.assertEqual(driver.advance(3600), [])
        self.assertEqual(len(run.steps), 2)
        records = run.take_records()
        self.assertEqual([r["event"] for r in records if r["event"] in ("start", "end")],
                         ["start", "end"])
        end = next(r for r in records if r["event"] == "end")
        self.assertEqual((end["reason"], end["valid"], end["user_id"]),
                         ("cancelled", False, USER))
        self.assertEqual([r["disposition"] for r in records if r["event"] == "reply"],
                         ["accepted", "late", "late", "duplicate"])

        # Abbrechen in a message ends the run the same way; the message stays answered.
        run = self.run_for("module", ("A1",))
        driver = Driver(run)
        entry = driver.advance()[0]
        disposition, out = driver.answer(entry, calibration.CANCEL)
        self.assertEqual((disposition, [m.kind for m in out]), ("accepted", ["invalid"]))
        self.assertEqual((run.phase, run.end_reason), ("invalid", "cancelled"))
        self.assertEqual(driver.answer(entry)[0], "duplicate")
        self.assertEqual(run.steps[0]["outcome"], "cancelled")

    def test_every_interruption_before_the_last_step_is_invalid(self):
        later = T0 + timedelta(hours=2)
        for reason, text in calibration.INTERRUPTIONS.items():
            waiting = self.run_for("module", ("A1",))
            running = self.run_for("module", ("A1",))
            Driver(running).advance()
            pause = self.run_for(modules=("P", "A1"))
            self.assertEqual(Driver(pause).follow().kind, "pause")
            for run in (waiting, running, pause):
                self.assertEqual(
                    [m.message for m in run.interrupt(later, reason)],
                    [f"Kalibrierung ungültig: {text}. Sie wird nicht ausgewertet."])
                self.assertEqual((run.phase, run.end_reason, run.records[-1]["valid"]),
                                 ("invalid", reason, False))
        finished = self.run_for("module", ("A1",))
        Driver(finished).follow()
        self.assertEqual(finished.interrupt(later, "session_ended"), [])
        self.assertTrue(finished.valid)

    def test_only_uninterrupted_protocols_are_evaluated(self):
        finished = self.run_for("module", ("A1",), run_id="finished")
        Driver(finished).follow()
        cancelled = self.run_for("module", ("A1",), run_id="cancelled")
        driver = Driver(cancelled)
        driver.answer(driver.advance()[0])
        cancelled.interrupt(driver.now, "cancelled")
        # Without an end record the run stopped uncleanly.
        unclean = self.run_for("module", ("A1",), run_id="unclean")
        driver = Driver(unclean)
        driver.answer(driver.advance()[0])
        # A requested repetition that has not run is a step still to come.
        repeated = self.run_for("module", ("P",), run_id="repeated")
        driver = Driver(repeated)
        driver.answer(driver.follow(), calibration.REPEAT)
        repeated.interrupt(driver.now, "session_ended")
        records = [record for run in (finished, cancelled, unclean, repeated)
                   for record in run.take_records()]
        steps, skipped = calibration.protocol_steps(records)
        self.assertEqual([(s["run_id"], s["step"]) for s in steps],
                         [("finished", step.key) for step in calibration.MODULES["A1"].steps])
        self.assertEqual(skipped, ["cancelled", "unclean", "repeated"])

    def test_duplicate_reply_from_a_second_copy_does_not_advance(self):
        run = self.run_for(modules=("A1",))
        driver = Driver(run)
        entry = driver.advance()[0]
        first = ids(run, entry, "phone")
        second = ids(run, entry, "tablet")
        self.assertEqual(run.reply(first[calibration.REPLY], at(20), USER)[0], "accepted")
        self.assertEqual(run.reply(second[calibration.REPLY], at(25), USER)[0], "duplicate")
        self.assertEqual(run.reply(second[calibration.DEVIATE], at(26), USER)[0], "duplicate")
        self.assertEqual(len(run.steps), 1)
        self.assertEqual(run.steps[0]["closed_at"], at(20))
        self.assertEqual(run.current.due_at, at(20 + 360))

    def test_late_reply_after_the_step_moved_on_is_ignored(self):
        run = self.run_for(modules=("P",))
        driver = Driver(run)
        empty = driver.advance()[0]
        driver.answer(empty)
        door = driver.advance(600)[0]
        driver.answer(door)
        away = driver.advance()[0]
        self.assertEqual(away.actions[0][0], calibration.DEVIATE)
        closing = driver.advance(120)[-1]
        self.assertEqual(closing.kind, "result")
        actions = ids(run, away, "late-copy")
        self.assertEqual(run.reply(actions[calibration.DEVIATE], driver.now, USER)[0], "late")
        self.assertEqual([s["outcome"] for s in run.steps], ["reply", "reply", "hold"])

    def test_repeat_of_module_p(self):
        run = self.run_for()
        driver = Driver(run)
        pause = driver.follow()
        self.assertEqual(driver.answer(pause, calibration.REPEAT)[0], "accepted")
        self.assertEqual([e.module.module_id for e in run.executions], ["P", "P"])
        again = driver.follow()
        self.assertEqual(driver.answer(pause, calibration.REPEAT)[0], "duplicate")
        driver.answer(again, calibration.NEXT)
        self.assertEqual(run.current.module.module_id, "A1")

        single = self.run_for("module", ("P",))
        driver = Driver(single)
        result = driver.follow()
        self.assertEqual((single.phase, result.kind), ("finished", "result"))
        self.assertEqual(dict(result.actions), {calibration.REPEAT: "Wiederholen"})
        actions = ids(single, result, "result")
        self.assertEqual(single.reply(actions[calibration.REPEAT], driver.now, USER,
                                      restart_allowed=False)[0], "late")
        self.assertEqual(single.reply(actions[calibration.REPEAT], driver.now, USER)[0],
                         "accepted")
        self.assertEqual(single.phase, "waiting")
        self.assertEqual(driver.advance(gang_active=True), [])
        self.assertEqual(driver.advance(gang_active=False)[0].step, "empty")
        self.assertEqual(len(single.executions), 2)

    def test_single_module_starts_only_when_no_gang_is_active(self):
        run = self.run_for("module", ("A1",))
        driver = Driver(run)
        self.assertEqual(run.waiting_for, "free")
        self.assertEqual(driver.advance(gang_active=True), [])
        self.assertEqual(driver.advance(600, gang_active=True), [])
        first = driver.advance(1, gang_active=False)
        self.assertEqual(first[0].step, "entry")
        self.assertEqual(run.executions[0].started_at, at(601))
        # A started module is not interrupted by its own gang.
        driver.answer(first[0])
        self.assertEqual(driver.advance(360, gang_active=True)[0].step, "infusion")

    def test_deviating_step_leaves_the_reference(self):
        run = self.run_for(modules=("A1",))
        driver = Driver(run)
        driver.answer(driver.advance()[0])
        infusion = driver.advance(360)[0]
        self.assertEqual(driver.answer(infusion, calibration.DEVIATE)[0], "accepted")
        self.assertEqual(run.steps[-1]["outcome"], "deviate")
        self.assertEqual(run.steps[-1]["action"], "Abweichend")
        result = calibration.evaluate(run.steps, presence=[], events=[
            ("infusion", infusion.at + timedelta(seconds=5))], gangs=[], gang_heat=[],
            until=driver.now, has_presence=False)
        self.assertEqual(result["totals"]["excluded_steps"], 1)
        self.assertEqual(result["totals"]["events"]["additional"], 0)

    def test_status_names_module_step_and_what_the_run_waits_for(self):
        run = self.run_for(modules=("P", "A1"))
        driver = Driver(run)

        def shown():
            status = run.status()
            self.assertNotIn("user_id", status)
            return tuple(status[key] for key in ("module", "step", "steps", "waiting"))

        self.assertEqual((run.status()["kind"], run.status()["modules"]),
                         ("session", ["P", "A1"]))
        self.assertEqual(shown(), ("P", None, 3, "ready"))
        driver.answer(driver.advance()[0])
        # The next instruction follows after its waiting time.
        self.assertEqual(shown(), ("P", 2, 3, "hold"))
        front = driver.advance(600)[0]
        self.assertEqual(shown(), ("P", 2, 3, "reply"))
        driver.answer(front)
        self.assertEqual(driver.advance()[0].step, "away")
        # A step without action ends with its waiting time.
        self.assertEqual(shown(), ("P", 3, 3, "hold"))
        pause = driver.advance(120)[-1]
        self.assertEqual((pause.kind, shown()), ("pause", ("P", 3, 3, "reply")))
        driver.answer(pause, calibration.NEXT)
        self.assertEqual(shown(), ("A1", 1, 4, "hold"))
        driver.advance()
        self.assertEqual(shown(), ("A1", 1, 4, "reply"))
        run.interrupt(driver.now, "cancelled", user_id=USER)
        self.assertEqual(run.status(), {
            "kind": "session", "modules": ["P", "A1"], "started_at": T0, "valid": False,
            "reason": "cancelled", "reason_text": "abgebrochen", "ended_at": driver.now})

        single = self.run_for("module", ("A1",))
        self.assertEqual((single.status()["module"], single.status()["waiting"]),
                         ("A1", "gang_end"))
        Driver(single).follow()
        self.assertEqual(
            {key: single.status()[key] for key in ("valid", "reason", "reason_text")},
            {"valid": True, "reason": "finished", "reason_text": None})

    def test_without_presence_sensor_p_is_omitted(self):
        self.assertEqual([m.module_id for m in calibration.select_modules(None, presence=False)],
                         ["A1", "A2", "A3", "CB", "D"])
        with self.assertRaises(ValueError):
            calibration.select_modules(("P",), presence=False)
        with self.assertRaises(ValueError):
            calibration.select_modules(("A1", "A1"), presence=True)
        run = self.run_for("module", ("A1",), presence=False)
        closing = Driver(run).follow()
        self.assertEqual(closing.message, "Modul A1 beendet, ohne Präsenzmessung.")

    def test_reply_targets_require_reply_capability_of_the_starting_user(self):
        mobile = RecipientRef("device", "phone", "home_assistant")
        plain = RecipientRef("entity", "notify.plain", "home_assistant")
        send = CapabilityEvidence("test", "send")
        resolutions = (
            RecipientResolution(mobile, ResolvedRecipient(
                mobile, TargetIdentity("home_assistant", "phone"),
                RecipientCapability(send=send, reply=CapabilityEvidence("test", "reply")))),
            RecipientResolution(plain, ResolvedRecipient(
                plain, TargetIdentity("home_assistant", "notify.plain"),
                RecipientCapability(send=send))),
        )
        assignments = (RecipientAssignment(USER, frozenset((mobile, plain))),
                       RecipientAssignment("other", frozenset((mobile,))))
        self.assertEqual(calibration.reply_targets(USER, assignments, resolutions),
                         (TargetIdentity("home_assistant", "phone"),))
        self.assertEqual(calibration.reply_targets(None, assignments, resolutions), ())
        self.assertEqual(calibration.reply_targets(USER, assignments, resolutions[1:]), ())


class CalibrationEvaluationTests(unittest.TestCase):
    def protocol(self, module, windows, run_id="run", execution=1):
        steps = calibration.MODULES[module].steps
        return [{"run_id": run_id, "execution": execution, "module": module, "step": step.key,
                 "reference": step.reference, "handoff_at": at(a), "closed_at": at(b),
                 "outcome": "reply" if step.action else "hold"}
                for step, (a, b) in zip(steps, windows, strict=True)]

    def recognized(self, module, windows, events):
        """Status, time and offset per expectation, additional ones, merged ones and totals."""
        result = calibration.evaluate(self.protocol(module, windows), presence=[],
                                      events=events, gangs=[], gang_heat=[], until=at(3600),
                                      has_presence=False)
        found = {(item["step"], e["kind"]): (e["status"], e["at"], e["offset_s"])
                 for item in result["executions"][0]["recognition"] for e in item["expected"]}
        return (found, [(e["kind"], e["at"]) for e in result["additional"]], result["merged"],
                result["totals"]["events"])

    def test_events_presence_and_gang_against_the_windows(self):
        # A1: entry 0–20, infusion 380–390, exit opening 420–430, exit closing 475–480.
        steps = self.protocol("A1", A1)
        events = [("door_open", at(5)), ("door_close", at(15)), ("infusion", at(395)),
                  ("door_open", at(400)), ("door_open", at(428)), ("door_close", at(500))]
        presence = [(at(0), False), (at(12), True), (at(200), False), (at(230), True),
                    (at(505), False)]
        gangs = [{"start": at(15), "confirmed": at(395), "end": at(470),
                  "end_reason": "ventilation", "infusions": 1}]
        result = calibration.evaluate(steps, presence=presence, events=events, gangs=gangs,
                                      gang_heat=[(at(100), at(450))], until=at(900))
        execution = result["executions"][0]
        entry, infusion, exit_open, exit_close = execution["recognition"]
        self.assertTrue(all(item["status"] == "found" and item["offset_s"] == 0
                            for item in entry["expected"]))
        # Recognized 5 s after the reply: assigned outside the window.
        self.assertEqual((infusion["expected"][0]["status"], infusion["expected"][0]["offset_s"]),
                         ("outside", 5.0))
        # Of two openings eligible for the exit, the one in its window is found.
        self.assertEqual((exit_open["expected"][0]["at"], exit_open["expected"][0]["offset_s"]),
                         (at(428), 0.0))
        self.assertEqual(result["additional"],
                         [{"run_id": "run", "kind": "door_open", "at": at(400)}])
        # Recognitions after the last window of a run are not considered.
        self.assertEqual(exit_close["expected"][0]["status"], "missing")
        self.assertEqual(result["totals"]["events"],
                         {"reference": 5, "found": 3, "outside": 1, "missing": 1,
                          "additional": 1, "merged": 0})
        self.assertEqual(calibration.evaluation_summary(result), (
            "Kalibrierung ausgewertet. Gänge 1 von 1 gefunden, 0 zusätzlich. "
            "Türen und Aufgüsse 3 von 5 im Fenster gefunden, 1 außerhalb des Fensters "
            "zugeordnet, 1 zusätzlich. Anwesenheit bei leerer Kabine: 0-mal. "
            "Einzelheiten im Archiv."))
        dropouts = execution["presence"]["dropouts"]
        self.assertEqual((dropouts["count"], dropouts["durations_s"]), (1, [30.0]))
        self.assertEqual(execution["presence"]["after_run_s"], 25.0)
        gang = execution["gang"]
        self.assertEqual((gang["found"], gang["additional"], gang["infusions"],
                          gang["reference_infusions"]), (True, 0, 1, 1))
        self.assertEqual(gang["end_offset_s"], 40.0)
        self.assertEqual(gang["gang_heat_after_exit_s"], 20.0)

    def test_opening_recognized_before_its_handoff_belongs_to_its_step(self):
        # As in probe W-2 4.4: the exit opening 7.8 s before the handoff, after a
        # spurious door operation. The section rule made it additional to the
        # infusion and the exit opening missing.
        found, additional, _, totals = self.recognized("A1", A1, [
            ("door_open", at(5)), ("door_close", at(15)), ("infusion", at(385)),
            ("door_open", at(395)), ("door_close", at(399)),
            ("door_open", at(412.2)), ("door_close", at(478))])
        self.assertEqual(found["exit_open", "door_open"], ("outside", at(412.2), -7.8))
        self.assertEqual(found["exit_close", "door_close"], ("found", at(478), 0.0))
        self.assertEqual(additional, [("door_open", at(395)), ("door_close", at(399))])
        self.assertEqual(totals, {"reference": 5, "found": 4, "outside": 1, "missing": 0,
                                  "additional": 2, "merged": 0})

    def test_recognitions_that_blocked_each_other_are_both_assigned(self):
        # The first infusion recognized after its reply and the second before its
        # handoff: the section rule put both into the second pour, with both
        # references missing.
        found, additional, _, totals = self.recognized("D", D, [
            ("door_open", at(5)), ("door_close", at(15)), ("infusion", at(215)),
            ("infusion", at(220)), ("infusion", at(527)),
            ("door_open", at(605)), ("door_close", at(675))])
        self.assertEqual(found["infusion_1", "infusion"], ("outside", at(215), 5.0))
        self.assertEqual(found["infusion_2", "infusion"], ("outside", at(527), -3.0))
        # The second pour of the double pour stays additional (E-17).
        self.assertEqual(additional, [("infusion", at(220))])
        self.assertEqual(totals, {"reference": 6, "found": 4, "outside": 2, "missing": 0,
                                  "additional": 1, "merged": 0})

    def test_each_recognition_is_assigned_on_its_own_without_blocking(self):
        # C/B: the closing of the short opening is missed. Bound to that opening,
        # the closing in the window of the opening in the decay stayed additional
        # (W-2a). On its own it is found there; the recognition merged both
        # door operations.
        found, additional, merged, totals = self.recognized("CB", CB, [
            ("door_open", at(5)), ("door_close", at(15)), ("door_open", at(82)),
            ("door_close", at(88)), ("door_open", at(272)), ("door_close", at(485)),
            ("infusion", at(405)), ("door_open", at(612)), ("door_close", at(685))])
        self.assertEqual(found["short_opening", "door_open"], ("found", at(272), 0.0))
        self.assertEqual(found["short_opening", "door_close"], ("missing", None, None))
        self.assertEqual(found["decay_opening", "door_open"], ("missing", None, None))
        self.assertEqual(found["decay_opening", "door_close"], ("found", at(485), 0.0))
        self.assertEqual(additional, [])
        self.assertEqual(merged, [{
            "run_id": "run",
            "opening": {"at": at(272), "execution": 1, "step": "short_opening"},
            "closing": {"at": at(485), "execution": 1, "step": "decay_opening"}}])
        self.assertEqual(totals, {"reference": 11, "found": 9, "outside": 0, "missing": 2,
                                  "additional": 0, "merged": 1})
        # A1: one recognized door operation from the entry opening to a closing
        # 5 s before the exit closing. The closing goes to the nearest fitting
        # window, not with +450 s to the entry (W-2a).
        found, additional, merged, totals = self.recognized("A1", A1, [
            ("door_open", at(10)), ("infusion", at(385)), ("door_close", at(470))])
        self.assertEqual(found["entry", "door_open"], ("found", at(10), 0.0))
        self.assertEqual(found["entry", "door_close"], ("missing", None, None))
        self.assertEqual(found["exit_close", "door_close"], ("outside", at(470), -5.0))
        self.assertEqual([(m["opening"]["step"], m["closing"]["step"]) for m in merged],
                         [("entry", "exit_close")])
        self.assertEqual(totals, {"reference": 5, "found": 2, "outside": 1, "missing": 2,
                                  "additional": 0, "merged": 1})

    def test_additional_recognition_and_missing_reference_remain(self):
        found, additional, _, totals = self.recognized("A1", A1, [
            ("door_open", at(5)), ("door_close", at(15)), ("infusion", at(385)),
            ("infusion", at(395)), ("door_open", at(425))])
        self.assertEqual(found["infusion", "infusion"], ("found", at(385), 0.0))
        # The entry closing lies in its own window, not after it: no exit closing.
        self.assertEqual(found["exit_close", "door_close"], ("missing", None, None))
        self.assertEqual(additional, [("infusion", at(395))])
        self.assertEqual(totals, {"reference": 5, "found": 4, "outside": 0, "missing": 1,
                                  "additional": 1, "merged": 0})

    def test_assignment_keeps_the_order_without_crossing(self):
        # Both infusions lie nearer to the second reference; each reference takes
        # one of them in their order, both outside their windows.
        found, additional, _, totals = self.recognized("D", D, [
            ("infusion", at(400)), ("infusion", at(410))])
        self.assertEqual(found["infusion_1", "infusion"], ("outside", at(400), 190.0))
        self.assertEqual(found["infusion_2", "infusion"], ("outside", at(410), -120.0))
        self.assertEqual(additional, [])
        self.assertEqual((totals["found"], totals["outside"]), (0, 2))

    def test_assignment_does_not_cross_the_order_over_all_kinds(self):
        # The infusion lies before the exit opening with the door closed. An
        # infusion recognized after the exit opening is not assigned to it as
        # well; per kind both were assigned (W-2a). The nearer exit opening is.
        found, additional, _, _ = self.recognized("A1", A1, [
            ("door_open", at(5)), ("door_close", at(15)), ("door_open", at(415)),
            ("infusion", at(425))])
        self.assertEqual(found["exit_open", "door_open"], ("outside", at(415), -5.0))
        self.assertEqual(found["infusion", "infusion"], ("missing", None, None))
        self.assertEqual(additional, [("infusion", at(425))])
        # The same within a step: the opening comes before its closing. Of a
        # closing recognized before the opening one is assigned, on a full tie
        # the earlier recognition.
        found, additional, _, _ = self.recognized("A1", A1, [
            ("door_close", at(5)), ("door_open", at(15)), ("infusion", at(385))])
        self.assertEqual((found["entry", "door_open"], found["entry", "door_close"]),
                         (("missing", None, None), ("found", at(5), 0.0)))
        self.assertEqual(additional, [("door_open", at(15))])

    def test_gang_belongs_to_the_module_through_the_assigned_entry_closing(self):
        # C/B: entry recognized before the instruction to enter. The gang starts
        # at the closing assigned to the entry; by the rule of W-2 it was missing
        # and additional.
        steps = self.protocol("CB", CB)
        events = [("door_open", at(5)), ("door_close", at(15)), ("door_open", at(72)),
                  ("door_close", at(75)), ("infusion", at(405))]

        def gang(start):
            gangs = [{"start": at(start), "confirmed": at(405), "end": None,
                      "end_reason": None, "infusions": 1}]
            result = calibration.evaluate(steps, presence=[], events=events, gangs=gangs,
                                          gang_heat=[], until=at(900), has_presence=False)
            item = result["executions"][0]["gang"]
            return item["found"], item.get("start_offset_s"), item["additional"]

        self.assertEqual(gang(75), (True, -5.0, 0))
        # A start that is not the assigned closing, at the minimum temperature:
        # the rule of W-2 applies.
        self.assertEqual(gang(100), (True, 10.0, 0))
        self.assertEqual(gang(77), (False, None, 1))

    def test_empty_cabin_reports_and_missing_or_additional_gangs(self):
        steps = self.protocol("P", ((0, 30), (630, 760), (760, 880)))
        presence = [(at(0), False), (at(100), True), (at(104), False), (at(700), True),
                    (at(710), False)]
        result = calibration.evaluate(steps, presence=presence, events=[], gangs=[],
                                      gang_heat=[], until=at(900))
        empty = result["executions"][0]["presence"]["empty"]
        self.assertEqual([(item["reference"], item["reports"]) for item in empty],
                         [("leer", 1), ("Person vor der Tür", 1), ("leer", 0)])
        self.assertEqual(result["totals"]["empty_cabin_reports"], 2)
        self.assertIsNone(result["executions"][0]["gang"])

        cb = self.protocol("CB", CB, run_id="other")
        gangs = [{"start": at(15), "confirmed": None, "end": None, "end_reason": None,
                  "infusions": 0}]
        result = calibration.evaluate(cb, presence=[], events=[], gangs=gangs, gang_heat=[],
                                      until=at(900), has_presence=False)
        gang = result["executions"][0]["gang"]
        self.assertEqual((gang["found"], gang["additional"]), (False, 1))
        self.assertEqual(result["totals"]["gangs"],
                         {"reference": 1, "found": 0, "missing": 1, "additional": 1})

    def test_gang_heat_uses_booked_heating_during_the_gang_demand(self):
        decisions = [(at(0), True, "below_target"), (at(100), True, "gang_heat_demand"),
                     (at(300), False, "above_target")]
        self.assertEqual(calibration.gang_heat_intervals(
            decisions, [(at(50), at(150)), (at(200), None)], at(400)),
            [(at(100), at(150)), (at(200), at(300))])


class CalibrationRuntimeTests(unittest.TestCase):
    """Productive runtime methods; only the final notification transport is synthetic."""

    class Transport:
        def __init__(self):
            self.sent = []

        def prepare_handoff(self, notification, *, correlate=True):
            return notification

        async def dispatch(self, prepared):
            self.sent.append(prepared)
            return TransportHandoff(prepared, True, "synthetic handoff")

        def forget_session(self, session_id):
            pass

    def runtime(self, clock, *, reply=True):
        reference = RecipientRef("device", "synthetic-phone", "home_assistant")
        configuration = _runtime(clock).configuration
        configuration = replace(
            configuration, presence_source="ha_presence",
            bindings=Bindings({**configuration.bindings.values,
                               "presence": "binary_sensor.presence"}),
            notification_assignments=(RecipientAssignment(USER, frozenset((reference,))),),
        )
        runtime = SaunaRuntime(configuration, clock=lambda: clock[0])
        runtime.notification_entry_id = "synthetic-entry"
        runtime.notification_transport = self.Transport()
        runtime.notification_resolutions = (RecipientResolution(reference, ResolvedRecipient(
            reference, TargetIdentity("home_assistant", "synthetic-phone"),
            RecipientCapability(
                send=CapabilityEvidence("test", "send"),
                reply=CapabilityEvidence("test", "reply") if reply else None))),)
        return runtime

    @staticmethod
    async def settle(runtime):
        while runtime._calibration_tasks:
            await asyncio.gather(*tuple(runtime._calibration_tasks))
            # Completed tasks leave the set through their done callback.
            await asyncio.sleep(0)

    @staticmethod
    def association(notification, kind, at_):
        action = next(item["action"] for item in notification.payload["data"]["actions"]
                      if item["action"].endswith(kind.upper()))
        reply = ReplyEnvelope(None, None, None, None, None, ActorRef(USER, "HA event context"),
                              {"action": action}, observed_at=at_,
                              reply_key=ReplyKey("home_assistant", action))
        return ReplyAssociation(ReplyDisposition.ACCEPTED, reply, notification, True)

    def test_calibration_session_starts_automatic_operation_and_waits_for_ready(self):
        async def exercise():
            clock = [START]
            runtime = self.runtime(clock)
            runtime.controller.set_temperature(60, START)
            await runtime.accept_presence(binary_presence(
                "binary_sensor.presence", "off", START, START))
            result = await runtime.start_calibration(actor=ActorRef(USER, "ha_context"),
                                                     modules=["A1"])
            self.assertEqual(result["modules"], ["A1"])
            self.assertTrue(runtime.session.operation_enabled)
            self.assertEqual(runtime.controller.control_mode, "automatic")
            await self.settle(runtime)
            self.assertEqual(runtime.notification_transport.sent, [])
            with self.assertRaisesRegex(ValueError, "bereits"):
                await runtime.start_calibration(actor=ActorRef(USER, "ha_context"),
                                                module="A2")
            clock[0] += timedelta(seconds=30)
            runtime.controller.set_temperature(80, clock[0])
            await runtime.tick()
            await self.settle(runtime)
            self.assertEqual(runtime.controller.phase, "bereit")
            sent = runtime.notification_transport.sent
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0].payload["message"], "Gang A1: hinein, Tür zu.")
            self.assertEqual(sent[0].session_id, runtime.session.session_id)
            self.assertEqual([a["title"] for a in sent[0].payload["data"]["actions"]],
                             ["Drin, Tür zu", "Abweichend", "Abbrechen"])
            disposition = await runtime.calibration_reply(
                self.association(sent[0], "reply", clock[0] + timedelta(seconds=15)))
            self.assertEqual(disposition, "accepted")
            self.assertEqual(await runtime.calibration_reply(
                self.association(sent[0], "reply", clock[0] + timedelta(seconds=16))),
                "duplicate")
            run = runtime.calibration_runs[0]
            self.assertEqual(run.steps[0]["handoff_at"], clock[0])
            clock[0] += timedelta(seconds=15 + 359)
            await runtime.tick()
            await self.settle(runtime)
            self.assertEqual(len(sent), 1)
            clock[0] += timedelta(seconds=1)
            await runtime.tick()
            await self.settle(runtime)
            self.assertEqual(sent[-1].payload["message"], "Aufguss, eine Kelle.")
            await runtime.cancel_calibration(actor=ActorRef(USER, "ha_context"))
            await self.settle(runtime)
            self.assertEqual((run.phase, run.end_reason), ("invalid", "cancelled"))
            self.assertEqual(sent[-1].payload, {
                "title": "Sauna-Kalibrierung",
                "message": "Kalibrierung ungültig: abgebrochen. Sie wird nicht ausgewertet."})
            self.assertTrue(runtime.session.operation_enabled)
            clock[0] += timedelta(seconds=600)
            await runtime.tick()
            await self.settle(runtime)
            self.assertEqual(len(sent), 3)
            await runtime.close()

        asyncio.run(exercise())

    def test_start_conditions(self):
        async def exercise():
            clock = [START]
            runtime = self.runtime(clock, reply=False)
            with self.assertRaisesRegex(ValueError, "Companion-Ziel"):
                await runtime.start_calibration(actor=ActorRef(USER, "ha_context"))
            self.assertIsNone(runtime.session)
            runtime = self.runtime(clock)
            with self.assertRaisesRegex(ValueError, "laufenden Sitzung"):
                await runtime.start_calibration(actor=ActorRef(USER, "ha_context"),
                                                module="A1")
            with self.assertRaisesRegex(ValueError, "Companion-Ziel"):
                await runtime.start_calibration(actor=ActorRef(None, "ha_context"))
            runtime.controller.set_temperature(60, START)
            await runtime.set_operation(True)
            with self.assertRaisesRegex(ValueError, "ohne laufende Sitzung"):
                await runtime.start_calibration(actor=ActorRef(USER, "ha_context"))
            with self.assertRaisesRegex(ValueError, "Es läuft keine"):
                await runtime.cancel_calibration()
            await runtime.close()

        asyncio.run(exercise())

    async def sensor_gang(self, runtime, clock, *, calibrate):
        """Entry with presence, infusion, exit and ventilation; optionally one module."""
        sid = runtime.session.session_id

        async def presence(value, seconds):
            clock[0] = START + timedelta(seconds=seconds)
            await runtime.accept_presence(binary_presence(
                "binary_sensor.presence", value, clock[0], clock[0]))

        async def receive(kind, seconds, name):
            clock[0] = START + timedelta(seconds=seconds)
            await runtime.receive(Event(
                name, sid, kind, clock[0], clock[0],
                door_close_evidence=DoorCloseEvidence(clock[0], clock[0])
                if kind == Kind.DOOR_CLOSE else None))

        await receive(Kind.DOOR_OPEN, 10, "recognition:entry-open")
        await presence("on", 12)
        await receive(Kind.DOOR_CLOSE, 14, "recognition:entry-close")
        self.assertIsNotNone(runtime.session.timeline.active)
        if calibrate:
            await runtime.start_calibration(actor=ActorRef(USER, "ha_context"), module="A1")
        await receive(Kind.INFUSION, 200, "recognition:water")
        await receive(Kind.DOOR_OPEN, 600, "recognition:exit-open")
        await presence("off", 620)
        await receive(Kind.VENTILATION, 650, "recognition:ventilation")
        self.assertIsNone(runtime.session.timeline.active)
        clock[0] = START + timedelta(seconds=651)
        await runtime.tick()

    def test_single_module_waits_for_the_gang_and_leaves_control_unchanged(self):
        async def exercise(calibrate):
            clock = [START]
            runtime = self.runtime(clock)
            runtime.controller.set_temperature(80, START)
            await runtime.set_operation(True, actor=ActorRef(USER, "ha_context"))
            await self.sensor_gang(runtime, clock, calibrate=calibrate)
            await self.settle(runtime)
            sent = runtime.notification_transport.sent
            if calibrate:
                # The module waited for the end of the running gang: it starts in
                # the cycle that books the ventilation.
                self.assertEqual(len(sent), 1)
                self.assertEqual(sent[0].payload["message"], "Gang A1: hinein, Tür zu.")
                self.assertEqual(runtime.calibration_runs[0].executions[0].started_at,
                                 START + timedelta(seconds=650))
            else:
                self.assertEqual(sent, [])
            # Session identities are random; everything else must be identical.
            outcome = (
                [(d.at, d.heat, d.reason) for d in runtime.controller.decisions],
                encoded(runtime.session.timeline).replace(runtime.session.session_id, "S"),
                runtime.controller.phase,
            )
            await runtime.close()
            return outcome

        self.assertEqual(asyncio.run(exercise(True)), asyncio.run(exercise(False)))

    async def module_in_session(self, clock):
        """A single module A1 that starts after a sensor gang; its entry is answered."""
        runtime = self.runtime(clock)
        runtime.controller.set_temperature(80, START)
        await runtime.set_operation(True, actor=ActorRef(USER, "ha_context"))
        await self.sensor_gang(runtime, clock, calibrate=True)
        await self.settle(runtime)
        first = runtime.notification_transport.sent[0]
        clock[0] += timedelta(seconds=10)
        await runtime.calibration_reply(self.association(first, "reply", clock[0]))
        return runtime, runtime.calibration_runs[0]

    async def complete(self, runtime, clock):
        """Answer each further instruction 5 s after its handoff until the run ends."""
        run, sent = runtime.calibration_runs[0], runtime.notification_transport.sent
        while run.active:
            await self.settle(runtime)
            execution = run.current
            if execution.sent_at is None:
                clock[0] = max(clock[0], execution.due_at)
                await runtime.tick()
                continue
            clock[0] = execution.sent_at + timedelta(seconds=5)
            await runtime.calibration_reply(self.association(sent[-1], "reply", clock[0]))
        await self.settle(runtime)

    async def end_session(self, runtime, clock):
        runtime.controller.finish_session(clock[0] + timedelta(seconds=20))
        clock[0] += timedelta(seconds=21)
        await runtime.tick()
        await self.settle(runtime)

    def test_session_end_evaluates_a_completed_run_and_sends_the_result(self):
        async def exercise():
            clock = [START]
            runtime, run = await self.module_in_session(clock)
            await self.complete(runtime, clock)
            self.assertEqual((run.phase, run.valid), ("finished", True))
            self.assertTrue(runtime.notification_transport.sent[-1].payload["message"]
                            .startswith("Gang A1 beendet."))
            await self.end_session(runtime, clock)
            self.assertEqual(runtime.calibration_runs, ())
            self.assertEqual((run.phase, run.end_reason), ("finished", "finished"))
            final = runtime.notification_transport.sent[-1]
            self.assertTrue(final.payload["message"].startswith("Kalibrierung ausgewertet."))
            self.assertNotIn("data", final.payload)
            await runtime.close()

        asyncio.run(exercise())

    def test_status_keeps_the_last_result_after_the_session_end(self):
        async def exercise():
            clock = [START]
            runtime, run = await self.module_in_session(clock)
            status = runtime.calibration_status
            self.assertEqual([item["id"] for item in status["modules"]],
                             ["P", "A1", "A2", "A3", "CB", "D"])
            self.assertEqual(
                {key: status["active"][key]
                 for key in ("kind", "module", "step", "steps", "waiting")},
                {"kind": "module", "module": "A1", "step": 2, "steps": 4,
                 "waiting": "hold"})
            self.assertIsNone(status["last"])
            await self.complete(runtime, clock)
            status = runtime.calibration_status
            self.assertIsNone(status["active"])
            self.assertEqual((status["last"]["valid"], status["last"]["totals"]),
                             (True, None))
            await self.end_session(runtime, clock)
            # The run has left the runtime; its evaluation totals remain shown.
            self.assertEqual(runtime.calibration_runs, ())
            last = runtime.calibration_status["last"]
            self.assertEqual((last["valid"], last["reason"], last["ended_at"]),
                             (True, "finished", run.ended_at))
            self.assertEqual(
                calibration.evaluation_summary({"totals": last["totals"]}),
                runtime.notification_transport.sent[-1].payload["message"])
            await runtime.close()

            clock = [START]
            runtime, run = await self.module_in_session(clock)
            await runtime.cancel_calibration(actor=ActorRef(USER, "ha_context"))
            await self.end_session(runtime, clock)
            last = runtime.calibration_status["last"]
            self.assertEqual(
                (last["valid"], last["reason"], last["reason_text"], last["totals"]),
                (False, "cancelled", "abgebrochen", None))
            await runtime.close()

            # Without a presence sensor module P is not offered.
            runtime = _runtime(clock)
            self.assertEqual([item["id"] for item in runtime.calibration_status["modules"]],
                             ["A1", "A2", "A3", "CB", "D"])
            await runtime.close()

        asyncio.run(exercise())

    def test_status_names_the_state_that_would_end_a_module_at_once(self):
        actor = ActorRef(USER, "ha_context")

        async def started(runtime, reason):
            # The lock reason is the reason the started module ends with.
            self.assertEqual(runtime.calibration_status["blocked"], {
                "reason": reason, "reason_text": calibration.INTERRUPTIONS[reason]})
            await runtime.start_calibration(actor=actor, module="A1")
            await self.settle(runtime)
            run = runtime.calibration_runs[-1]
            self.assertEqual((run.phase, run.end_reason), ("invalid", reason))

        async def exercise():
            clock = [START]
            runtime = self.runtime(clock)
            runtime.controller.set_temperature(80, START)
            entry = SimpleNamespace(runtime_data=runtime,
                                    options=runtime.configuration.as_options())
            await async_set_control_mode(_FakeHass(), entry, "manual", actor=actor)
            # Without a session the calibration session stays startable.
            self.assertIsNone(runtime.calibration_status["blocked"])
            await runtime.accept_presence(binary_presence(
                "binary_sensor.presence", "off", START, START))
            await runtime.set_heater_override(True, actor=actor)
            self.assertIsNotNone(runtime.session)
            await started(runtime, "manual_mode")
            await runtime.close()

            clock = [START]
            runtime = self.runtime(clock)
            runtime.controller.set_temperature(80, START)
            await runtime.set_operation(True, actor=actor)
            await runtime.accept_presence(binary_presence(
                "binary_sensor.presence", "off", START, START))
            self.assertIsNone(runtime.calibration_status["blocked"])
            await runtime.set_heater_override(False, actor=actor)
            await started(runtime, "heater_override")
            await runtime.set_heater_override(None, actor=actor)
            self.assertIsNone(runtime.calibration_status["blocked"])
            for state in ("unknown", "unavailable"):
                clock[0] += timedelta(seconds=5)
                await runtime.accept_presence(binary_presence(
                    "binary_sensor.presence", state, clock[0], clock[0]))
                await started(runtime, "presence_unavailable")
            clock[0] += timedelta(seconds=5)
            await runtime.accept_presence(binary_presence(
                "binary_sensor.presence", "on", clock[0], clock[0]))
            self.assertIsNone(runtime.calibration_status["blocked"])
            await runtime.close()

            # Without a presence sensor no module needs a presence state.
            runtime = _runtime(clock)
            runtime.controller.set_temperature(80, clock[0])
            await runtime.set_operation(True, actor=actor)
            self.assertIsNone(runtime.calibration_status["blocked"])
            await runtime.close()

        asyncio.run(exercise())

    def test_session_end_before_the_last_step_makes_the_run_invalid(self):
        async def exercise():
            clock = [START]
            runtime, run = await self.module_in_session(clock)
            await self.end_session(runtime, clock)
            self.assertEqual(runtime.calibration_runs, ())
            self.assertEqual((run.phase, run.end_reason), ("invalid", "session_ended"))
            messages = [item.payload for item in runtime.notification_transport.sent]
            self.assertEqual(messages[-1], {
                "title": "Sauna-Kalibrierung",
                "message": "Kalibrierung ungültig: Sitzung vor dem letzten Schritt beendet. "
                           "Sie wird nicht ausgewertet."})
            self.assertFalse(any(m["message"].startswith("Kalibrierung ausgewertet")
                                 for m in messages))
            await runtime.close()

        asyncio.run(exercise())

    def test_operation_off_makes_the_run_invalid_without_further_instruction(self):
        async def exercise():
            clock = [START]
            runtime, run = await self.module_in_session(clock)
            sent = runtime.notification_transport.sent
            clock[0] += timedelta(seconds=20)
            await runtime.set_operation(False, actor=ActorRef(USER, "ha_context"))
            await self.settle(runtime)
            self.assertEqual((run.phase, run.end_reason), ("invalid", "operation_off"))
            self.assertEqual([item.payload["message"] for item in sent[1:]],
                             ["Kalibrierung ungültig: Betrieb aus. Sie wird nicht ausgewertet."])
            # The session waits for resumption; no instruction follows, not even then.
            self.assertFalse(runtime.session.operation_enabled)
            clock[0] += timedelta(seconds=400)
            await runtime.tick()
            await runtime.set_operation(True, actor=ActorRef(USER, "ha_context"))
            clock[0] += timedelta(seconds=400)
            await runtime.tick()
            await self.settle(runtime)
            self.assertEqual((len(sent), run.phase), (2, "invalid"))
            await self.end_session(runtime, clock)
            self.assertEqual(len(sent), 2)
            await runtime.close()

        asyncio.run(exercise())

    def test_unload_makes_the_run_invalid_without_message(self):
        async def exercise():
            clock = [START]
            runtime, run = await self.module_in_session(clock)
            await runtime.close()
            self.assertEqual((run.phase, run.end_reason), ("invalid", "integration_unloaded"))
            self.assertEqual(len(runtime.notification_transport.sent), 1)

        asyncio.run(exercise())

    def test_manual_heater_selection_makes_the_run_invalid(self):
        async def exercise():
            clock = [START]
            runtime, run = await self.module_in_session(clock)
            sent = runtime.notification_transport.sent
            clock[0] += timedelta(seconds=20)
            await runtime.set_heater_override(False, actor=ActorRef(USER, "ha_context"))
            await self.settle(runtime)
            self.assertEqual((run.phase, run.end_reason), ("invalid", "heater_override"))
            self.assertEqual([item.payload["message"] for item in sent[1:]], [
                "Kalibrierung ungültig: manuelle Ofenwahl. Sie wird nicht ausgewertet."])
            # Returning to automatic does not make it valid again.
            await runtime.set_heater_override(None, actor=ActorRef(USER, "ha_context"))
            clock[0] += timedelta(seconds=400)
            await runtime.tick()
            await self.settle(runtime)
            self.assertEqual((len(sent), run.phase), (2, "invalid"))
            await runtime.close()

        asyncio.run(exercise())

    def test_manual_mode_makes_the_run_invalid(self):
        async def exercise():
            actor = ActorRef(USER, "ha_context")
            clock = [START]
            runtime, run = await self.module_in_session(clock)
            entry = SimpleNamespace(runtime_data=runtime,
                                    options=runtime.configuration.as_options())
            # In automatic operation the mode is locked; the run goes on.
            with self.assertRaisesRegex(ValueError, "ausgeschaltetem Automatikbetrieb"):
                await async_set_control_mode(_FakeHass(), entry, "manual", actor=actor)
            self.assertEqual(run.phase, "running")
            await runtime.close()

            # A module in a session in manual mode ends at once.
            clock = [START]
            runtime = self.runtime(clock)
            runtime.controller.set_temperature(80, START)
            entry = SimpleNamespace(runtime_data=runtime,
                                    options=runtime.configuration.as_options())
            await async_set_control_mode(_FakeHass(), entry, "manual", actor=actor)
            await runtime.accept_presence(binary_presence(
                "binary_sensor.presence", "off", START, START))
            await runtime.set_heater_override(True, actor=actor)
            self.assertTrue(runtime.session.operation_enabled)
            await runtime.start_calibration(actor=actor, module="A1")
            await self.settle(runtime)
            run = runtime.calibration_runs[0]
            self.assertEqual((run.phase, run.end_reason), ("invalid", "manual_mode"))
            self.assertEqual([item.payload["message"]
                              for item in runtime.notification_transport.sent], [
                "Kalibrierung ungültig: manueller Modus. Sie wird nicht ausgewertet."])
            await runtime.close()

        asyncio.run(exercise())

    def test_unknown_or_unavailable_presence_makes_the_run_invalid(self):
        text = ("Kalibrierung ungültig: Präsenzsensor unbekannt oder nicht verfügbar. "
                "Sie wird nicht ausgewertet.")

        async def exercise(state):
            clock = [START]
            runtime, run = await self.module_in_session(clock)
            sent = runtime.notification_transport.sent
            clock[0] += timedelta(seconds=20)
            await runtime.accept_presence(binary_presence(
                "binary_sensor.presence", state, clock[0], clock[0]))
            await self.settle(runtime)
            self.assertEqual((run.phase, run.end_reason), ("invalid", "presence_unavailable"))
            self.assertEqual([item.payload["message"] for item in sent[1:]], [text])
            # A later report of presence does not make it valid again.
            clock[0] += timedelta(seconds=5)
            await runtime.accept_presence(binary_presence(
                "binary_sensor.presence", "on", clock[0], clock[0]))
            clock[0] += timedelta(seconds=400)
            await runtime.tick()
            await self.settle(runtime)
            self.assertEqual((len(sent), run.phase), (2, "invalid"))
            await runtime.close()

        for state in ("unknown", "unavailable"):
            asyncio.run(exercise(state))

        async def without_report():
            # Without a reported state at the start the run ends at once.
            clock = [START]
            runtime = self.runtime(clock)
            runtime.controller.set_temperature(60, START)
            await runtime.start_calibration(actor=ActorRef(USER, "ha_context"), modules=["A1"])
            await self.settle(runtime)
            run = runtime.calibration_runs[0]
            self.assertEqual((run.phase, run.end_reason), ("invalid", "presence_unavailable"))
            self.assertEqual([item.payload["message"]
                              for item in runtime.notification_transport.sent], [text])
            await runtime.close()

        asyncio.run(without_report())


if __name__ == "__main__":
    unittest.main()
