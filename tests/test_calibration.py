"""Calibration runs: instructions, replies, protocol and evaluation (V31, W-2, W-2a, W-2b, W-3).

The basic procedure runs P1 while heating, Gang 1 after Bereit, P2 in the
pause after Gang 1, Gang 2 and the optional Gang 3; each part is valid on its
own (W3-2). The display state for the panel (W-2c) names part, step and
waiting reason of the running run and keeps the last result after its
session ended.

The run never operates the sauna. Runtime cases use the productive service
methods with a synthetic final transport and compare control and gang results
with the same inputs without a calibration run. An interruption ends the run
and makes only the running part invalid; completed parts are evaluated.
"""

import asyncio
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from types import SimpleNamespace

from test_notification_occasions import panel_button, panel_question
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
        self.ready, self.gang_active = True, False

    def advance(self, seconds=0, *, ready=None, gang_active=None):
        self.now += timedelta(seconds=seconds)
        out = self.run.advance(self.now, ready=self.ready if ready is None else ready,
                               gang_active=self.gang_active if gang_active is None
                               else gang_active)
        self.messages.extend(out)
        return out

    def answer(self, instruction, kind=calibration.REPLY, *, token=None, delay=None):
        self.now += timedelta(seconds=self.reply_after if delay is None else delay)
        actions = ids(self.run, instruction, token or f"m{instruction.seq}")
        disposition, out = self.run.reply(actions[kind], self.now, USER,
                                          message_id=token or f"m{instruction.seq}")
        self.messages.extend(out)
        return disposition, out

    def instruction(self):
        """The message of the step the running part waits for."""
        return next(m for m in reversed(self.messages) if m.seq == self.run.current.seq)

    def follow(self):
        """Run the current part to its end; return its closing message.

        Without a closing message (a part waits for its start) None.
        """
        start = len(self.messages)
        self.advance()
        while True:
            closing = next((m for m in self.messages[start:] if m.kind != "step"), None)
            if closing is not None:
                return closing
            if self.run.phase != "running":
                return None
            execution = self.run.current
            if execution.sent_at is None:
                self.advance(max((execution.due_at - self.now).total_seconds(), 0))
                continue
            step = execution.module.steps[execution.step]
            if step.action is None:
                # A step without action closes by its own time.
                self.advance(step.hold_seconds)
                continue
            self.answer(self.instruction())


def wind_timer(run, now=T0):
    """BN-a: the basic procedure begins with winding the timer; answered at once."""
    instruction = run.advance(now, ready=False, gang_active=False)[0]
    assert instruction.kind == "timer"
    action = ids(run, instruction, "timer")[calibration.TIMER]
    assert run.reply(action, now, USER, message_id="timer")[0] == "accepted"
    return instruction


def procedure_run(presence=True, run_id="run"):
    run = calibration.CalibrationRun(
        run_id, "procedure", calibration.procedure(presence=presence), USER, "session", T0,
        has_presence=presence, presence=[(T0, False)] if presence else [])
    wind_timer(run)
    return run


class CalibrationProcedureTests(unittest.TestCase):
    """Basic procedure W-3 with simulated replies; every part valid on its own (W3-2)."""

    def parts(self, run):
        return [(e.module.module_id, e.index in run.valid_executions) for e in run.executions]

    def test_the_basic_procedure_with_two_gangs(self):
        run = procedure_run()
        driver = Driver(run)
        driver.ready = False
        start = run.take_records()[0]
        self.assertEqual({key: start[key] for key in ("kind", "modules", "optional", "validity")},
                         {"kind": "procedure", "modules": ["P1", "G1", "P2", "G2"],
                          "optional": ["G3"], "validity": "part"})
        # W3-6: P1 begins at once while heating, before Bereit.
        p1 = driver.follow()
        self.assertEqual(driver.messages[0].message, "Kabine leer, Tür zu, nicht an die Kabine.")
        self.assertEqual(driver.messages[0].title, "Sauna-Kalibrierung P1")
        self.assertEqual((p1.kind, p1.message, p1.actions), (
            "part", "P1: keine Anwesenheit bei leerer Kabine. Gang 1 beginnt nach Bereit.", ()))
        # Gang 1 waits for Bereit.
        self.assertEqual(run.waiting_for, "ready")
        self.assertEqual(run.status()["waiting"], "ready")
        self.assertEqual(driver.advance(600), [])
        driver.ready = True
        entry = driver.advance()[0]
        self.assertEqual((entry.title, entry.message),
                         ("Sauna-Kalibrierung Gang 1", "Gang 1: hinein, Tür zu."))
        gang1 = driver.follow()
        self.assertEqual(gang1.kind, "part")
        self.assertTrue(gang1.message.startswith("Gang 1 beendet."))
        # P2 follows the exit closing of Gang 1 at once (W3-6).
        driver.advance()
        p2_first = driver.messages[-1]
        self.assertEqual((p2_first.title, p2_first.message, p2_first.at),
                         ("Sauna-Kalibrierung P2", "Kabine leer, Tür zu, nicht an die Kabine.",
                          gang1.at))
        pause = driver.follow()
        self.assertEqual((pause.kind, pause.message), (
            "pause", ("P2: keine Anwesenheit bei leerer Kabine. Pause. Gang 2 beginnt mit "
                      "„Nächster Gang“.")))
        self.assertEqual(dict(pause.actions), {calibration.NEXT: "Nächster Gang",
                                               calibration.CANCEL: "Abbrechen"})
        self.assertEqual(driver.answer(pause, calibration.NEXT)[0], "accepted")
        visit = driver.advance()[0]
        self.assertEqual(visit.message, "Gang 2: Tür öffnen, kurz hinein, etwas ablegen, "
                                        "wieder hinaus, Tür zu.")
        pause = driver.follow()
        self.assertEqual(pause.kind, "pause")
        self.assertTrue(pause.message.endswith(
            "Pause. „Nächster Gang“ beginnt den freiwilligen Gang 3, „Beenden“ schließt die "
            "Kalibrierung ab."))
        self.assertEqual(dict(pause.actions), {calibration.NEXT: "Nächster Gang",
                                               calibration.FINISH: "Beenden"})
        self.assertEqual(run.status()["actions"], [
            {"action": "next", "label": "Nächster Gang"}, {"action": "finish", "label": "Beenden"}])
        disposition, out = driver.answer(pause, calibration.FINISH)
        self.assertEqual((disposition, [(m.kind, m.message) for m in out]), ("accepted", [(
            "result", ("Kalibrierung abgeschlossen. Die Auswertung der Erkennung folgt nach "
                       "dem Sitzungsende."))]))
        self.assertEqual((run.phase, run.end_reason, run.valid), ("ended", "finished", True))
        self.assertEqual(self.parts(run), [("P1", True), ("G1", True), ("P2", True), ("G2", True)])
        self.assertEqual(run.status()["modules"], ["P1", "Gang 1", "P2", "Gang 2"])
        # Steps and waiting times as in the tables; "+5 min" counts from the reply.
        g1 = [s for s in run.steps if s["module"] == "G1"]
        self.assertEqual([s["step"] for s in g1], [
            "entry", "infusion_1", "short_opening", "infusion_2", "exit_open", "exit_close"])
        self.assertEqual([(b["handoff_at"] - a["closed_at"]).total_seconds()
                          for a, b in pairwise(g1)], [300, 180, 120, 30, 45])
        g2 = [s for s in run.steps if s["module"] == "G2"]
        self.assertEqual([(b["handoff_at"] - a["closed_at"]).total_seconds()
                          for a, b in pairwise(g2)], [60, 300, 60, 240, 90, 180])
        self.assertTrue(all(step["outcome"] in ("reply", "hold") for step in run.steps))
        # Each step message offers Abweichend and Abbrechen.
        steps = [m for m in driver.messages if m.kind == "step"]
        self.assertTrue(all({calibration.DEVIATE, calibration.CANCEL} <= dict(m.actions).keys()
                            for m in steps))
        records = run.take_records()
        self.assertEqual([(r["module"], r["valid"]) for r in records if r["event"] == "module_end"],
                         [("P1", True), ("G1", True), ("P2", True), ("G2", True)])
        end = next(r for r in records if r["event"] == "end")
        self.assertEqual((end["reason"], end["valid"], end["valid_executions"]),
                         ("finished", True, [1, 2, 3, 4]))

    def test_the_optional_third_gang(self):
        run = procedure_run()
        driver = Driver(run)
        for _ in range(3):
            closing = driver.follow()
        self.assertEqual(closing.kind, "pause")
        driver.answer(closing, calibration.NEXT)
        closing = driver.follow()
        driver.answer(closing, calibration.NEXT)
        first = driver.advance()[0]
        self.assertEqual((first.title, first.message),
                         ("Sauna-Kalibrierung Gang 3", "Gang 3: hinein, Tür zu."))
        result = driver.follow()
        self.assertEqual(result.kind, "result")
        self.assertTrue(result.message.startswith("Gang 3 beendet."))
        self.assertTrue(result.message.endswith(
            "Kalibrierung abgeschlossen. Die Auswertung der Erkennung folgt nach dem "
            "Sitzungsende."))
        self.assertEqual((run.end_reason, run.valid), ("finished", True))
        self.assertEqual(run.status()["modules"], ["P1", "Gang 1", "P2", "Gang 2", "Gang 3"])
        g3 = [s for s in run.steps if s["module"] == "G3"]
        self.assertEqual([s["step"] for s in g3], [
            "entry", "infusion_1", "waving", "resting", "infusion_2", "exit_open",
            "exit_close", "ventilation_open", "ventilation_close"])
        # "sofort" after the reply: waving and the second infusion.
        self.assertEqual([(b["handoff_at"] - a["closed_at"]).total_seconds()
                          for a, b in pairwise(g3)], [60, 0, 240, 0, 60, 20, 120, 60])
        module = calibration.MODULES["G3"]
        roles = {step.key: (step.expects, step.role) for step in module.steps}
        # W-3a (Nutzer 10.11.): exit opening and closing with a reply each, as in
        # Gang 1 and 2; the after-run of Gang 3 is measurable.
        self.assertEqual(roles["exit_open"], (("door_open",), "exit_open"))
        self.assertEqual(roles["exit_close"], (("door_close",), "exit_close"))
        exit_open = next(step for step in module.steps if step.key == "exit_open")
        self.assertEqual((exit_open.message, exit_open.action),
                         ("Hinaus, Tür offen lassen.", "Draußen, Tür offen"))
        # W3-4/W3-5: ventilation with a short entry; the window closes with the door.
        self.assertEqual(roles["ventilation_open"], (("door_open",), None))
        self.assertEqual(roles["ventilation_close"], (("door_close",), None))
        self.assertEqual((roles["waving"], roles["resting"]), (((), None), ((), None)))

    def test_an_interruption_in_gang_2_keeps_p1_gang_1_and_p2_valid(self):
        for reason, text in (("cancelled", "abgebrochen"), ("operation_off", "Betrieb aus")):
            run = procedure_run(run_id=reason)
            driver = Driver(run)
            for _ in range(3):
                closing = driver.follow()
            driver.answer(closing, calibration.NEXT)
            driver.answer(driver.advance()[0])
            entry = driver.advance(60)[0]
            self.assertEqual(entry.step, "entry")
            notice = run.interrupt(driver.now, reason, user_id=USER)
            self.assertEqual([(m.kind, m.actions, m.message) for m in notice], [(
                "interrupted", (),
                (f"Kalibrierung unterbrochen: {text}. Gang 2 ist ungültig; die abgeschlossenen "
                 "Teile werden nach dem Sitzungsende ausgewertet."))])
            self.assertEqual(self.parts(run),
                             [("P1", True), ("G1", True), ("P2", True), ("G2", False)])
            self.assertEqual((run.valid, run.evaluable, run.invalid_execution), (False, True, 4))
            self.assertEqual(run.status()["parts"],
                             {"valid": ["P1", "Gang 1", "P2"], "invalid": "Gang 2"})
            # The open step is recorded without reference; no instruction follows.
            self.assertEqual(run.steps[-1]["outcome"], "cancelled")
            self.assertEqual(driver.answer(entry)[0], "late")
            self.assertEqual(driver.advance(3600), [])
            self.assertEqual({s["module"] for s in run.valid_steps}, {"P1", "G1", "P2"})
            records = run.take_records()
            self.assertEqual(
                [(r["module"], r["valid"], r["reason"]) for r in records
                 if r["event"] == "module_end"][-1], ("G2", False, reason))
            steps, skipped = calibration.protocol_steps(records)
            self.assertEqual(steps, [dict(s) for s in run.valid_steps])
            self.assertEqual(skipped, [{"run_id": reason, "execution": 4, "module": "G2"}])
            # Evaluated are the valid parts only, with the unchanged rules.
            result = calibration.evaluate(run.valid_steps, presence=run.presence, events=[],
                                          gangs=[], gang_heat=[], until=driver.now)
            self.assertEqual([item["module"] for item in result["executions"]],
                             ["P1", "G1", "P2"])
            self.assertEqual(result["totals"]["gangs"]["reference"], 1)

    def test_cancel_in_p1_leaves_no_valid_part_and_a_pause_cancel_none_invalid(self):
        run = procedure_run()
        driver = Driver(run)
        empty = driver.advance()[0]
        disposition, out = driver.answer(empty, calibration.CANCEL)
        self.assertEqual((disposition, [(m.kind, m.message) for m in out]), ("accepted", [(
            "invalid", "Kalibrierung ungültig: abgebrochen. Sie wird nicht ausgewertet.")]))
        self.assertEqual((run.evaluable, run.valid, run.status()["parts"]),
                         (False, False, {"valid": [], "invalid": "P1"}))
        # Cancelled in the pause after P2: no part runs, all completed ones stay valid.
        run = procedure_run()
        driver = Driver(run)
        for _ in range(3):
            pause = driver.follow()
        disposition, out = driver.answer(pause, calibration.CANCEL)
        self.assertEqual([m.message for m in out], [(
            "Kalibrierung beendet: abgebrochen. Die abgeschlossenen Teile werden nach dem "
            "Sitzungsende ausgewertet.")])
        self.assertEqual((run.valid, run.end_reason, run.invalid_execution),
                         (True, "cancelled", None))
        # While Gang 1 waits for Bereit, the session ends: P1 stays valid.
        run = procedure_run()
        driver = Driver(run)
        driver.ready = False
        driver.follow()
        run.interrupt(driver.now, "session_ended")
        self.assertEqual((self.parts(run), run.valid), ([("P1", True)], True))

    def test_late_and_duplicate_replies_in_the_procedure(self):
        run = procedure_run()
        driver = Driver(run)
        for _ in range(3):
            pause = driver.follow()
        phone, tablet = ids(run, pause, "phone"), ids(run, pause, "tablet")
        self.assertEqual(run.reply(phone[calibration.NEXT], driver.now, USER)[0], "accepted")
        # The same pause from a second device, and the panel, change nothing.
        self.assertEqual(run.reply(tablet[calibration.NEXT], driver.now, USER)[0], "duplicate")
        self.assertEqual(run.answer_pause(calibration.NEXT, driver.now, USER),
                         ("unavailable", []))
        self.assertEqual([e.module.module_id for e in run.executions], ["P1", "G1", "P2", "G2"])
        # A late reply to a step of P2 after it moved on.
        p2_door = next(m for m in driver.messages if m.title.endswith("P2") and m.step == "door_front")
        self.assertEqual(driver.answer(p2_door)[0], "duplicate")
        p2_away = next(m for m in driver.messages if m.title.endswith("P2") and m.step == "away")
        self.assertEqual(driver.answer(p2_away, calibration.DEVIATE, token="late")[0], "late")
        self.assertEqual(run.phase, "running")
        # The panel answers an open pause like its message (W-3).
        closing = driver.follow()
        self.assertEqual(closing.kind, "pause")
        self.assertEqual(run.answer_pause(calibration.CANCEL, driver.now, USER),
                         ("unavailable", []))
        disposition, out = run.answer_pause(calibration.FINISH, driver.now, USER)
        self.assertEqual((disposition, out[0].kind), ("accepted", "result"))
        reply = [r for r in run.records if r["event"] == "reply"][-1]
        self.assertEqual((reply["action"], reply["source"], reply["message_id"]),
                         ("finish", "panel", None))
        self.assertEqual(driver.answer(closing, calibration.NEXT)[0], "duplicate")
        self.assertEqual((run.end_reason, len(run.executions)), ("finished", 4))

    def test_status_names_part_step_and_what_the_run_waits_for(self):
        run = procedure_run()
        driver = Driver(run)
        driver.ready = False

        def shown():
            status = run.status()
            self.assertNotIn("user_id", status)
            return tuple(status[key] for key in ("module", "step", "steps", "waiting"))

        self.assertEqual((run.status()["kind"], run.status()["modules"]),
                         ("procedure", ["P1", "Gang 1", "P2", "Gang 2"]))
        self.assertEqual(shown(), ("P1", None, 3, "hold"))
        driver.answer(driver.advance()[0])
        # The next instruction follows after its waiting time.
        self.assertEqual(shown(), ("P1", 2, 3, "hold"))
        front = driver.advance(600)[0]
        self.assertEqual(shown(), ("P1", 2, 3, "reply"))
        driver.answer(front)
        self.assertEqual(driver.advance()[0].step, "away")
        # A step without action ends with its waiting time.
        self.assertEqual(shown(), ("P1", 3, 3, "hold"))
        driver.advance(120)
        self.assertEqual(shown(), ("Gang 1", None, 6, "ready"))
        driver.ready = True
        driver.advance()
        self.assertEqual(shown(), ("Gang 1", 1, 6, "reply"))
        driver.follow()
        driver.follow()
        self.assertEqual(shown(), ("P2", 3, 3, "pause"))
        self.assertEqual((run.status()["next"], run.status()["actions"]),
                         ("Gang 2", [{"action": "next", "label": "Nächster Gang"}]))
        run.interrupt(driver.now, "cancelled", user_id=USER)
        self.assertEqual(run.status(), {
            "kind": "procedure", "modules": ["P1", "Gang 1", "P2", "Gang 2"], "started_at": T0,
            "valid": True, "reason": "cancelled", "reason_text": "abgebrochen",
            "ended_at": driver.now, "parts": {"valid": ["P1", "Gang 1", "P2"], "invalid": None}})

        single = calibration.CalibrationRun(
            "single", "module", (calibration.ADDITIONAL["A1"],), USER, "session", T0,
            presence=[(T0, False)])
        self.assertEqual((single.status()["module"], single.status()["waiting"]),
                         ("A1", "gang_end"))
        Driver(single).follow()
        self.assertEqual(
            {key: single.status()[key] for key in ("valid", "reason", "reason_text")},
            {"valid": True, "reason": "finished", "reason_text": None})

    def test_without_presence_sensor_p1_and_p2_are_omitted(self):
        self.assertEqual([m.module_id for m in calibration.procedure(presence=False)],
                         ["G1", "G2", "G3"])
        run = procedure_run(presence=False)
        driver = Driver(run)
        driver.ready = False
        self.assertEqual(driver.advance(), [])
        self.assertEqual(run.waiting_for, "ready")
        driver.ready = True
        pause = driver.follow()
        self.assertEqual((pause.kind, pause.message), (
            "pause", ("Gang 1 beendet, ohne Präsenzmessung. Pause. Gang 2 beginnt mit "
                      "„Nächster Gang“.")))
        driver.answer(pause, calibration.NEXT)
        self.assertEqual(driver.follow().kind, "pause")


class CalibrationRunTests(unittest.TestCase):
    def run_for(self, module="A1", presence=True, run_id="run"):
        return calibration.CalibrationRun(
            run_id, "module", (calibration.additional_module(module, presence=presence),),
            USER, "session", T0, has_presence=presence,
            presence=[(T0, False)] if presence else [])

    def test_additional_modules_and_the_short_visit_of_cb(self):
        self.assertEqual(list(calibration.ADDITIONAL), ["P", "A1", "A2", "A3", "CB", "D"])
        with self.assertRaisesRegex(ValueError, "Unbekanntes Zusatzmodul"):
            calibration.additional_module("G1", presence=True)
        # W3-4: no door opening without a short entry; no empty-cabin role any more.
        visit = calibration.ADDITIONAL["CB"].steps[0]
        self.assertEqual((visit.key, visit.message, visit.reference, visit.action, visit.expects,
                          visit.role), (
            "outside_opening", ("Tür öffnen, kurz hinein, etwas ablegen oder holen, wieder "
                                "hinaus, Tür zu."), "kurzer Türvorgang ohne Gang", "Tür zu",
            ("door_open", "door_close"), None))
        self.assertEqual(calibration.MODULES["G2"].steps[0].key, visit.key)
        # Presence during the short visit is no presence at an empty cabin.
        steps = CalibrationEvaluationTests.protocol(None, "CB", CB)
        result = calibration.evaluate(steps, presence=[(at(0), False), (at(5), True),
                                                       (at(15), False)],
                                      events=[], gangs=[], gang_heat=[], until=at(900))
        self.assertEqual((result["executions"][0]["presence"]["empty"],
                          result["totals"]["empty_cabin_reports"]), ([], 0))
        run = self.run_for("CB")
        first = Driver(run).advance()[0]
        self.assertEqual((first.title, first.message, dict(first.actions)[calibration.REPLY]),
                         ("Sauna-Kalibrierung C/B", visit.message, "Tür zu"))

    def test_cancel_makes_the_run_invalid_without_further_instruction(self):
        run = self.run_for()
        driver = Driver(run)
        entry = driver.advance()[0]
        driver.answer(entry)
        infusion = driver.advance(360)[0]
        # The service cancels outside any message; only the notice of invalidity follows.
        notice = run.interrupt(driver.now, "cancelled", user_id=USER)
        self.assertEqual([(m.kind, m.actions, m.message) for m in notice], [(
            "invalid", (), "Kalibrierung ungültig: abgebrochen. Sie wird nicht ausgewertet.")])
        self.assertEqual(run.interrupt(driver.now, "cancelled"), [])
        self.assertEqual((run.phase, run.valid), ("ended", False))
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
        run = self.run_for()
        driver = Driver(run)
        entry = driver.advance()[0]
        disposition, out = driver.answer(entry, calibration.CANCEL)
        self.assertEqual((disposition, [m.kind for m in out]), ("accepted", ["invalid"]))
        self.assertEqual((run.phase, run.end_reason), ("ended", "cancelled"))
        self.assertEqual(driver.answer(entry)[0], "duplicate")
        self.assertEqual(run.steps[0]["outcome"], "cancelled")

    def test_every_interruption_makes_only_the_running_part_invalid(self):
        later = T0 + timedelta(hours=2)
        for reason, text in calibration.INTERRUPTIONS.items():
            waiting = self.run_for()
            running = self.run_for()
            Driver(running).advance()
            for run in (waiting, running):
                self.assertEqual(
                    [m.message for m in run.interrupt(later, reason)],
                    [f"Kalibrierung ungültig: {text}. Sie wird nicht ausgewertet."])
                self.assertEqual((run.phase, run.end_reason, run.records[-1]["valid"]),
                                 ("ended", reason, False))
            pause = procedure_run()
            driver = Driver(pause)
            for _ in range(3):
                driver.follow()
            gang = procedure_run()
            driver = Driver(gang)
            for _ in range(3):
                closing = driver.follow()
            driver.answer(closing, calibration.NEXT)
            driver.advance()
            self.assertEqual(
                [m.message for m in pause.interrupt(later, reason)],
                [(f"Kalibrierung beendet: {text}. Die abgeschlossenen Teile werden nach dem "
                  "Sitzungsende ausgewertet.")])
            self.assertEqual(
                [m.message for m in gang.interrupt(later, reason)],
                [(f"Kalibrierung unterbrochen: {text}. Gang 2 ist ungültig; die "
                  "abgeschlossenen Teile werden nach dem Sitzungsende ausgewertet.")])
            self.assertEqual((pause.valid_executions, gang.valid_executions), ((1, 2, 3),) * 2)
            self.assertEqual((pause.invalid_execution, gang.invalid_execution), (None, 4))
        finished = self.run_for()
        Driver(finished).follow()
        self.assertEqual(finished.interrupt(later, "session_ended"), [])
        self.assertTrue(finished.valid)

    def test_only_valid_parts_are_evaluated(self):
        finished = self.run_for(run_id="finished")
        Driver(finished).follow()
        cancelled = self.run_for(run_id="cancelled")
        driver = Driver(cancelled)
        driver.answer(driver.advance()[0])
        cancelled.interrupt(driver.now, "cancelled")
        # Without an end record the run stopped uncleanly; its running part is invalid.
        unclean = self.run_for(run_id="unclean")
        driver = Driver(unclean)
        driver.answer(driver.advance()[0])
        # W3-2: a requested repetition that has not run leaves the completed P valid.
        repeated = self.run_for("P", run_id="repeated")
        driver = Driver(repeated)
        driver.answer(driver.follow(), calibration.REPEAT)
        repeated.interrupt(driver.now, "session_ended")
        records = [record for run in (finished, cancelled, unclean, repeated)
                   for record in run.take_records()]
        # An older run without part validity counts as a whole (readable protocols).
        old = [{"event": "start", "run_id": "old", "at": T0, "kind": "session",
                "modules": ["A1"]},
               *[{**step, "run_id": "old"} for step in finished.steps],
               {"event": "end", "run_id": "old", "at": T0, "reason": "finished", "valid": True},
               {"event": "start", "run_id": "old-invalid", "at": T0, "kind": "session",
                "modules": ["A1"]},
               *[{**step, "run_id": "old-invalid"} for step in finished.steps],
               {"event": "end", "run_id": "old-invalid", "at": T0, "reason": "cancelled",
                "valid": False}]
        steps, skipped = calibration.protocol_steps(records + old)
        self.assertEqual([(s["run_id"], s["step"]) for s in steps],
                         [("finished", step.key) for step in calibration.MODULES["A1"].steps]
                         + [("repeated", step.key) for step in calibration.MODULES["P"].steps]
                         + [("old", step.key) for step in calibration.MODULES["A1"].steps])
        self.assertEqual(skipped, [
            {"run_id": "cancelled", "execution": 1, "module": "A1"},
            {"run_id": "unclean", "execution": 1, "module": "A1"},
            {"run_id": "old-invalid", "execution": None, "module": None}])

    def test_duplicate_reply_from_a_second_copy_does_not_advance(self):
        run = self.run_for()
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
        run = self.run_for("P")
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
        single = self.run_for("P")
        driver = Driver(single)
        result = driver.follow()
        self.assertEqual((single.phase, result.kind), ("ended", "result"))
        self.assertEqual(result.message, "Modul P: keine Anwesenheit bei leerer Kabine.")
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
        again = driver.follow()
        self.assertEqual(driver.answer(result, calibration.REPEAT)[0], "duplicate")
        self.assertEqual((again.kind, single.valid_executions), ("result", (1, 2)))

    def test_single_module_starts_only_when_no_gang_is_active(self):
        run = self.run_for()
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
        run = self.run_for()
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

    def test_without_presence_sensor_p_is_omitted(self):
        self.assertEqual([m.module_id for m in calibration.additional_modules(presence=False)],
                         ["A1", "A2", "A3", "CB", "D"])
        with self.assertRaises(ValueError):
            calibration.additional_module("P", presence=False)
        run = self.run_for(presence=False)
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

    def test_after_run_of_gang_1_reaches_the_reply_kabine_ist_leer_of_p2(self):
        # W-3a (Nutzer 10.11.): P2 begins at the exit closing of Gang 1; the
        # after-run window reaches the reply „Kabine ist leer“ of P2. Before, it
        # ended at the handoff of P2 and was not measurable.
        gang1 = ((0, 20), (320, 330), (510, 520), (640, 660), (690, 695), (740, 745))
        p2 = ((745, 760), (1360, 1480), (1480, 1600))

        def evaluate(off, outcome="reply"):
            steps = self.protocol("G1", gang1) + self.protocol("P2", p2, execution=2)
            steps[6]["outcome"] = outcome
            presence = [(at(0), False), (at(10), True), (at(off), False)]
            result = calibration.evaluate(steps, presence=presence, events=[], gangs=[],
                                          gang_heat=[], until=at(3600))
            return result["executions"]

        gang, empty = evaluate(755)
        self.assertEqual((gang["presence"]["after_run_s"], gang["presence"]["after_run_off"]),
                         (10.0, at(755)))
        # The empty cabin of P2 is measured from its reply, unchanged.
        first = empty["presence"]["empty"][0]
        self.assertEqual((first["reference"], first["from"], first["reports"]),
                         ("leer", at(760), 0))
        # Absence only after the reply, or a deviating first step of P2: as before.
        self.assertIsNone(evaluate(770)[0]["presence"]["after_run_s"])
        self.assertIsNone(evaluate(755, "deviate")[0]["presence"]["after_run_s"])
        # The other results keep their windows: P2 without the deviating step.
        self.assertEqual(evaluate(755, "deviate")[1]["presence"]["empty"][0]["reference"],
                         "Person vor der Tür")

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

        def prepare_handoff(self, notification, *, correlate=True, answers=()):
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

    def instruction(self, runtime, run):
        """The first copy of the message the running step waits for."""
        return next(item for item in runtime.notification_transport.sent
                    if item.correlation_id == f"calibration:{run.run_id}:{run.current.seq}")

    async def proceed(self, runtime, clock, *, pause=calibration.NEXT, until=None):
        """Answer each instruction 5 s after its handoff, in a pause with ``pause``.

        Returns once the run ended, waits for Bereit or a gang end, or ``until(run)``.
        """
        run = runtime.calibration_runs[-1]
        sent = runtime.notification_transport.sent
        while run.active and not (until and until(run)):
            await self.settle(runtime)
            if run.phase == "waiting":
                return
            if run.phase == "timer":
                # BN-a: the procedure begins with winding the timer.
                clock[0] += timedelta(seconds=5)
                timer = next(item for item in reversed(sent)
                             if item.correlation_id.startswith(f"calibration:{run.run_id}:")
                             and item.payload["message"] == calibration.TIMER_MESSAGE)
                await runtime.calibration_reply(self.association(timer, "timer", clock[0]))
                continue
            if run.phase == "pause":
                clock[0] += timedelta(seconds=5)
                pause_message = next(item for item in reversed(sent)
                                     if item.correlation_id.startswith(
                                         f"calibration:{run.run_id}:")
                                     and "data" in item.payload)
                await runtime.calibration_reply(self.association(pause_message, pause, clock[0]))
                continue
            execution = run.current
            step = execution.module.steps[execution.step]
            if execution.sent_at is None or step.action is None:
                due = (execution.due_at if execution.sent_at is None
                       else execution.sent_at + timedelta(seconds=step.hold_seconds))
                clock[0] = max(clock[0], due)
                await runtime.tick()
                continue
            clock[0] = max(clock[0], execution.sent_at + timedelta(seconds=5))
            await runtime.calibration_reply(
                self.association(self.instruction(runtime, run), "reply", clock[0]))
        await self.settle(runtime)

    def test_calibration_starts_with_p1_while_heating_and_gang_1_after_ready(self):
        async def exercise():
            clock = [START]
            runtime = self.runtime(clock)
            runtime.notification_apple = lambda target: True
            runtime.controller.set_temperature(30, START)
            await runtime.accept_presence(binary_presence(
                "binary_sensor.presence", "off", START, START))
            result = await runtime.start_calibration(actor=ActorRef(USER, "ha_context"))
            self.assertEqual((result["kind"], result["modules"]),
                             ("procedure", ["P1", "G1", "P2", "G2", "G3"]))
            self.assertTrue(runtime.session.operation_enabled)
            self.assertEqual(runtime.controller.control_mode, "automatic")
            await self.settle(runtime)
            # BN-a: first the timer, no reference; its reply starts P1.
            sent = runtime.notification_transport.sent
            self.assertEqual([(item.payload["title"], item.payload["message"]) for item in sent],
                             [("Sauna-Kalibrierung", "Zeitschaltuhr aufziehen.")])
            self.assertEqual([a["title"] for a in sent[0].payload["data"]["actions"]],
                             ["Uhr ist gestellt", "Abbrechen", "Im Panel öffnen"])
            # BN-c: on an Apple device no tap target; the panel button opens the question.
            data = sent[0].payload["data"]
            self.assertEqual(set(data), {"actions"})
            self.assertEqual(data["actions"][-1], panel_button(
                data["actions"][-1]["uri"], f"HA_SAUNA_PANEL_{sent[0].message_id}"))
            self.assertEqual(panel_question(data["actions"][-1]["uri"]), {
                "e": "synthetic-entry", "t": "Sauna-Kalibrierung",
                "m": "Zeitschaltuhr aufziehen.", "a": data["actions"][:2]})
            self.assertEqual(runtime.calibration_status["active"]["module"], "Zeitschaltuhr")
            self.assertEqual(await runtime.calibration_reply(
                self.association(sent[0], "timer", START)), "accepted")
            await self.settle(runtime)
            # W3-6: P1 begins at once, while the sauna heats.
            self.assertEqual([(item.payload["title"], item.payload["message"])
                              for item in sent[1:]],
                             [("Sauna-Kalibrierung P1",
                               "Kabine leer, Tür zu, nicht an die Kabine.")])
            self.assertEqual(sent[1].session_id, runtime.session.session_id)
            self.assertEqual([a["title"] for a in sent[1].payload["data"]["actions"]],
                             ["Kabine ist leer", "Abweichend", "Abbrechen", "Im Panel öffnen"])
            # BN-d: on Apple the panel button follows three answers too (Android limit only).
            self.assertEqual(set(sent[1].payload["data"]), {"actions"})
            with self.assertRaisesRegex(ValueError, "bereits"):
                await runtime.start_calibration(actor=ActorRef(USER, "ha_context"),
                                                module="A2")
            await self.proceed(runtime, clock)
            run = runtime.calibration_runs[0]
            self.assertEqual(runtime.calibration_status["active"]["waiting"], "ready")
            self.assertEqual(sent[-1].payload["message"],
                             "P1: keine Anwesenheit bei leerer Kabine. Gang 1 beginnt nach Bereit.")
            count = len(sent)
            clock[0] += timedelta(seconds=30)
            await runtime.tick()
            await self.settle(runtime)
            self.assertEqual(len(sent), count)
            # Gang 1 begins after Bereit; the minimum temperature is reached by then.
            runtime.controller.set_temperature(80, clock[0])
            await runtime.tick()
            await self.settle(runtime)
            self.assertEqual(runtime.controller.phase, "bereit")
            self.assertIsNotNone(runtime.controller.minimum_temperature_reached_at)
            self.assertEqual(sent[-1].payload["message"], "Gang 1: hinein, Tür zu.")
            self.assertEqual(await runtime.calibration_reply(
                self.association(sent[-1], "reply", clock[0] + timedelta(seconds=15))),
                "accepted")
            self.assertEqual(await runtime.calibration_reply(
                self.association(sent[-1], "reply", clock[0] + timedelta(seconds=16))),
                "duplicate")
            # The panel cannot answer a pause that is not open.
            with self.assertRaisesRegex(ValueError, "wartet nicht"):
                await runtime.answer_calibration_pause(
                    "next", run_id=run.run_id, actor=ActorRef(USER, "ha_context"))
            with self.assertRaisesRegex(ValueError, "läuft nicht mehr"):
                await runtime.answer_calibration_pause(
                    "next", run_id="other", actor=ActorRef(USER, "ha_context"))
            clock[0] += timedelta(seconds=20)
            await runtime.cancel_calibration(actor=ActorRef(USER, "ha_context"))
            await self.settle(runtime)
            self.assertEqual((run.phase, run.end_reason, run.valid_executions,
                              run.invalid_execution), ("ended", "cancelled", (1,), 2))
            self.assertEqual(sent[-1].payload, {
                "title": "Sauna-Kalibrierung",
                "message": "Kalibrierung unterbrochen: abgebrochen. Gang 1 ist ungültig; die "
                           "abgeschlossenen Teile werden nach dem Sitzungsende ausgewertet."})
            self.assertTrue(runtime.session.operation_enabled)
            count = len(sent)
            clock[0] += timedelta(seconds=600)
            await runtime.tick()
            await self.settle(runtime)
            self.assertEqual(len(sent), count)
            # P1 is evaluated after the session end, Gang 1 is not.
            await self.end_session(runtime, clock)
            self.assertTrue(sent[-1].payload["message"].startswith("Kalibrierung ausgewertet."))
            last = runtime.calibration_status["last"]
            self.assertEqual((last["valid"], last["parts"], last["totals"]["gangs"]["reference"]),
                             (False, {"valid": ["P1"], "invalid": "Gang 1"}, 0))
            await runtime.close()

        asyncio.run(exercise())

    def test_the_basic_procedure_ends_with_beenden_and_an_additional_module_follows(self):
        async def exercise():
            clock = [START]
            runtime = self.runtime(clock)
            runtime.controller.set_temperature(80, START)
            await runtime.accept_presence(binary_presence(
                "binary_sensor.presence", "off", START, START))
            await runtime.start_calibration(actor=ActorRef(USER, "ha_context"))
            run = runtime.calibration_runs[0]
            sent = runtime.notification_transport.sent
            # Up to the pause after Gang 2: „Nächster Gang“ or „Beenden“.
            await self.proceed(runtime, clock, until=lambda item: (
                item.phase == "pause" and item.current.module.module_id == "G2"))
            status = runtime.calibration_status["active"]
            self.assertEqual((status["module"], status["waiting"], status["next"]),
                             ("Gang 2", "pause", "Gang 3"))
            self.assertEqual([item["action"] for item in status["actions"]], ["next", "finish"])
            # The panel answers the pause of the shown run.
            clock[0] += timedelta(seconds=30)
            self.assertEqual(await runtime.answer_calibration_pause(
                "finish", run_id=run.run_id, actor=ActorRef(USER, "ha_context")),
                {"run_id": run.run_id, "action": "finish"})
            await self.settle(runtime)
            self.assertEqual((run.end_reason, run.valid, run.valid_executions),
                             ("finished", True, (1, 2, 3, 4)))
            self.assertEqual(sent[-1].payload["message"], "Kalibrierung abgeschlossen. Die "
                             "Auswertung der Erkennung folgt nach dem Sitzungsende.")
            # A later answer to the pause message is a duplicate.
            pause = next(item for item in reversed(sent) if "data" in item.payload)
            self.assertEqual(await runtime.calibration_reply(
                self.association(pause, "next", clock[0])), "duplicate")
            # W3-1: an additional module starts later in the same session.
            await runtime.start_calibration(actor=ActorRef(USER, "ha_context"), module="CB")
            await self.settle(runtime)
            module = runtime.calibration_runs[-1]
            self.assertEqual((module.kind, sent[-1].payload["message"]), (
                "module", ("Tür öffnen, kurz hinein, etwas ablegen oder holen, wieder hinaus, "
                           "Tür zu.")))
            await self.proceed(runtime, clock)
            self.assertEqual((module.valid, module.valid_executions), (True, (1,)))
            await self.end_session(runtime, clock)
            evaluations = [item.payload["message"] for item in sent
                           if item.payload["message"].startswith("Kalibrierung ausgewertet.")]
            self.assertEqual(len(evaluations), 2)
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
            with self.assertRaisesRegex(ValueError, "Zusatzmodul startet in einer laufenden"):
                await runtime.start_calibration(actor=ActorRef(USER, "ha_context"),
                                                module="A1")
            with self.assertRaisesRegex(ValueError, "Companion-Ziel"):
                await runtime.start_calibration(actor=ActorRef(None, "ha_context"))
            runtime.controller.set_temperature(60, START)
            await runtime.set_operation(True)
            with self.assertRaisesRegex(ValueError, "Die Kalibrierung startet ohne laufende"):
                await runtime.start_calibration(actor=ActorRef(USER, "ha_context"))
            with self.assertRaisesRegex(ValueError, "Unbekanntes Zusatzmodul"):
                await runtime.start_calibration(actor=ActorRef(USER, "ha_context"),
                                                module="G1")
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

    def test_gang_cannot_be_ended_by_hand_during_calibration(self):
        # GE1a (Nutzer 10.11.2026): die Kalibrierung prüft gegen das Gangmodell.
        async def exercise():
            clock = [START]
            runtime = self.runtime(clock)
            runtime.controller.set_temperature(80, START)
            await runtime.set_operation(True, actor=ActorRef(USER, "ha_context"))
            sid = runtime.session.session_id
            for kind, seconds, name in ((Kind.DOOR_OPEN, 10, "entry-open"),
                                        (Kind.DOOR_CLOSE, 14, "entry-close")):
                if name == "entry-close":
                    clock[0] = START + timedelta(seconds=12)
                    await runtime.accept_presence(binary_presence(
                        "binary_sensor.presence", "on", clock[0], clock[0]))
                clock[0] = START + timedelta(seconds=seconds)
                await runtime.receive(Event(
                    name, sid, kind, clock[0], clock[0],
                    door_close_evidence=DoorCloseEvidence(clock[0], clock[0])
                    if kind == Kind.DOOR_CLOSE else None))
            gang = runtime.session.timeline.active
            self.assertIsNotNone(gang)
            await runtime.start_calibration(actor=ActorRef(USER, "ha_context"), module="A1")
            with self.assertRaisesRegex(ValueError, "Kalibrierung"):
                await runtime.end_gang(gang.gang_id)
            self.assertEqual(runtime.session.timeline.active, gang)
            await runtime.cancel_calibration(actor=ActorRef(USER, "ha_context"))
            await self.settle(runtime)
            clock[0] = START + timedelta(seconds=30)
            await runtime.end_gang(gang.gang_id)
            self.assertIsNone(runtime.session.timeline.active)
            await runtime.close()

        asyncio.run(exercise())

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
            self.assertEqual((run.phase, run.valid), ("ended", True))
            self.assertTrue(runtime.notification_transport.sent[-1].payload["message"]
                            .startswith("Gang A1 beendet."))
            await self.end_session(runtime, clock)
            self.assertEqual(runtime.calibration_runs, ())
            self.assertEqual((run.phase, run.end_reason), ("ended", "finished"))
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
            text = calibration.INTERRUPTIONS[reason]
            self.assertEqual(runtime.calibration_status["blocked"], {
                "reason": reason, "reason_text": text,
                "text": f"Ein jetzt gestartetes Zusatzmodul wäre sofort ungültig: {text}."})
            await runtime.start_calibration(actor=actor, module="A1")
            await self.settle(runtime)
            run = runtime.calibration_runs[-1]
            self.assertEqual((run.phase, run.end_reason), ("ended", reason))

        async def exercise():
            clock = [START]
            runtime = self.runtime(clock)
            runtime.controller.set_temperature(80, START)
            entry = SimpleNamespace(runtime_data=runtime,
                                    options=runtime.configuration.as_options())
            await async_set_control_mode(_FakeHass(), entry, "manual", actor=actor)
            await runtime.accept_presence(binary_presence(
                "binary_sensor.presence", "off", START, START))
            # Without a session the calibration session stays startable in manual mode.
            self.assertIsNone(runtime.calibration_status["blocked"])
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

    def test_w2e_start_locks_without_immediate_interruption_or_lost_gang(self):
        # W-2e (Nutzer 10.11.2026): L-G6a-2 und die Lücken aus W-2d.
        actor = ActorRef(USER, "ha_context")

        async def exercise():
            # Kalibriersitzung ohne bekannten Präsenzzustand
            clock = [START]
            runtime = self.runtime(clock)
            runtime.controller.set_temperature(80, START)
            text = calibration.INTERRUPTIONS["presence_unavailable"]
            self.assertIsNone(runtime.session)
            self.assertEqual(runtime.calibration_status["blocked"], {
                "reason": "presence_unavailable", "reason_text": text,
                "text": f"Eine jetzt gestartete Kalibrierung wäre sofort ungültig: {text}."})
            await runtime.accept_presence(binary_presence(
                "binary_sensor.presence", "off", START, START))
            self.assertIsNone(runtime.calibration_status["blocked"])
            await runtime.close()

            # Einzelnes Modul vor dem ersten Erreichen der Mindesttemperatur
            clock = [START]
            runtime = self.runtime(clock)
            minimum = runtime.configuration.parameters.values["sauna_min_temperature_c"]
            runtime.controller.set_temperature(minimum - 20, START)
            await runtime.accept_presence(binary_presence(
                "binary_sensor.presence", "off", START, START))
            await runtime.set_operation(True, actor=actor)
            self.assertIsNone(runtime.controller.minimum_temperature_reached_at)
            self.assertEqual(runtime.calibration_status["blocked"], {
                "reason": "minimum_temperature_pending",
                "reason_text": "Mindesttemperatur noch nicht erreicht",
                "text": "Zusatzmodul starten ist bis zum ersten Erreichen der "
                        "Mindesttemperatur gesperrt: vorher zählt kein Eintritt, der Gang des "
                        "Zusatzmoduls fehlte."})
            clock[0] += timedelta(seconds=30)
            runtime.controller.set_temperature(minimum + 1, clock[0])
            self.assertEqual(runtime.controller.minimum_temperature_reached_at, clock[0])
            self.assertIsNone(runtime.calibration_status["blocked"])
            # Einmal erreicht, bleibt das Modul startbar, auch wenn die Temperatur fällt.
            clock[0] += timedelta(seconds=30)
            runtime.controller.set_temperature(minimum - 5, clock[0])
            self.assertIsNone(runtime.calibration_status["blocked"])

            # Einzelnes Modul in der Wiederaufnahmefrist nach Betrieb AUS
            await runtime.set_operation(False, actor=actor)
            self.assertIsNotNone(runtime.session)
            text = calibration.INTERRUPTIONS["operation_off"]
            self.assertEqual(runtime.calibration_status["blocked"], {
                "reason": "operation_off", "reason_text": text,
                "text": f"Ein jetzt gestartetes Zusatzmodul wäre sofort ungültig: {text}."})
            # Der Dienst bleibt unverändert: ein solcher Start endet sofort ungültig.
            await runtime.start_calibration(actor=actor, module="A1")
            await self.settle(runtime)
            run = runtime.calibration_runs[-1]
            self.assertEqual((run.phase, run.end_reason), ("ended", "operation_off"))
            await runtime.close()

        asyncio.run(exercise())

    def test_session_end_before_the_last_step_makes_the_run_invalid(self):
        async def exercise():
            clock = [START]
            runtime, run = await self.module_in_session(clock)
            await self.end_session(runtime, clock)
            self.assertEqual(runtime.calibration_runs, ())
            self.assertEqual((run.phase, run.end_reason), ("ended", "session_ended"))
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
            self.assertEqual((run.phase, run.end_reason), ("ended", "operation_off"))
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
            self.assertEqual((len(sent), run.phase), (2, "ended"))
            await self.end_session(runtime, clock)
            self.assertEqual(len(sent), 2)
            await runtime.close()

        asyncio.run(exercise())

    def test_unload_makes_the_run_invalid_without_message(self):
        async def exercise():
            clock = [START]
            runtime, run = await self.module_in_session(clock)
            await runtime.close()
            self.assertEqual((run.phase, run.end_reason), ("ended", "integration_unloaded"))
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
            self.assertEqual((run.phase, run.end_reason), ("ended", "heater_override"))
            self.assertEqual([item.payload["message"] for item in sent[1:]], [
                "Kalibrierung ungültig: manuelle Ofenwahl. Sie wird nicht ausgewertet."])
            # Returning to automatic does not make it valid again.
            await runtime.set_heater_override(None, actor=ActorRef(USER, "ha_context"))
            clock[0] += timedelta(seconds=400)
            await runtime.tick()
            await self.settle(runtime)
            self.assertEqual((len(sent), run.phase), (2, "ended"))
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
            self.assertEqual((run.phase, run.end_reason), ("ended", "manual_mode"))
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
            self.assertEqual((run.phase, run.end_reason), ("ended", "presence_unavailable"))
            self.assertEqual([item.payload["message"] for item in sent[1:]], [text])
            # A later report of presence does not make it valid again.
            clock[0] += timedelta(seconds=5)
            await runtime.accept_presence(binary_presence(
                "binary_sensor.presence", "on", clock[0], clock[0]))
            clock[0] += timedelta(seconds=400)
            await runtime.tick()
            await self.settle(runtime)
            self.assertEqual((len(sent), run.phase), (2, "ended"))
            await runtime.close()

        for state in ("unknown", "unavailable"):
            asyncio.run(exercise(state))

        async def without_report():
            # Without a reported state at the start the run ends at once.
            clock = [START]
            runtime = self.runtime(clock)
            runtime.controller.set_temperature(60, START)
            await runtime.start_calibration(actor=ActorRef(USER, "ha_context"))
            await self.settle(runtime)
            run = runtime.calibration_runs[0]
            self.assertEqual((run.phase, run.end_reason), ("ended", "presence_unavailable"))
            self.assertEqual([item.payload["message"]
                              for item in runtime.notification_transport.sent], [text])
            await runtime.close()

        asyncio.run(without_report())


if __name__ == "__main__":
    unittest.main()
