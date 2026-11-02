"""Calibration runs: instructions, replies, protocol and evaluation (V31, W-2).

The run never operates the sauna. Runtime cases use the productive service
methods with a synthetic final transport and compare control and gang results
with the same inputs without a calibration run.
"""

import asyncio
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from test_presence_regressions import START, _runtime

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

T0 = datetime(2026, 11, 2, 12, tzinfo=UTC)
USER = "synthetic-user"


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
    def run_for(self, kind="session", modules=None, presence=True):
        selected = calibration.select_modules(modules, presence=presence)
        return calibration.CalibrationRun("run", kind, selected, USER, "session", T0,
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
        self.assertEqual(run.phase, "finished")
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

    def test_cancel_records_the_open_step_and_later_replies_are_late(self):
        run = self.run_for(modules=("A1",))
        driver = Driver(run)
        entry = driver.advance()[0]
        driver.answer(entry)
        infusion = driver.advance(360)[0]
        # The service cancels outside any message.
        self.assertTrue(run.cancel(driver.now, "cancelled", user_id=USER))
        self.assertFalse(run.cancel(driver.now, "cancelled"))
        self.assertEqual(run.phase, "cancelled")
        self.assertEqual([s["outcome"] for s in run.steps], ["reply", "cancelled"])
        self.assertEqual(driver.answer(infusion)[0], "late")
        self.assertEqual(driver.answer(infusion, calibration.CANCEL)[0], "late")
        self.assertEqual(driver.answer(entry)[0], "duplicate")
        self.assertEqual(driver.advance(3600), [])
        self.assertEqual(len(run.steps), 2)
        records = run.take_records()
        self.assertEqual([r["event"] for r in records if r["event"] in ("start", "end")],
                         ["start", "end"])
        self.assertEqual([r["disposition"] for r in records if r["event"] == "reply"],
                         ["accepted", "late", "late", "duplicate"])

        # Abbrechen in a message ends the run; the same message stays answered.
        run = self.run_for("module", ("A1",))
        driver = Driver(run)
        entry = driver.advance()[0]
        self.assertEqual(driver.answer(entry, calibration.CANCEL)[0], "accepted")
        self.assertEqual((run.phase, run.end_reason), ("cancelled", "cancelled"))
        self.assertEqual(driver.answer(entry)[0], "duplicate")
        self.assertEqual(run.steps[0]["outcome"], "cancelled")

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

    def test_events_presence_and_gang_against_the_windows(self):
        # A1: entry 0–20, infusion 380–390, exit opening 420–430, exit closing 475–480.
        steps = self.protocol("A1", ((0, 20), (380, 390), (420, 430), (475, 480)))
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
        self.assertTrue(all(item["found"] and item["offset_s"] == 0
                            for item in entry["expected"]))
        # Recognized 5 s after the reply, still before the next instruction.
        self.assertEqual(infusion["expected"][0]["offset_s"], 5.0)
        self.assertEqual(infusion["additional"], [{"kind": "door_open", "at": at(400)}])
        self.assertEqual(exit_open["expected"][0]["offset_s"], 0.0)
        # The final step of a run ends with its window.
        self.assertFalse(exit_close["expected"][0]["found"])
        self.assertEqual(result["totals"]["events"],
                         {"reference": 5, "found": 4, "missing": 1, "additional": 1})
        dropouts = execution["presence"]["dropouts"]
        self.assertEqual((dropouts["count"], dropouts["durations_s"]), (1, [30.0]))
        self.assertEqual(execution["presence"]["after_run_s"], 25.0)
        gang = execution["gang"]
        self.assertEqual((gang["found"], gang["additional"], gang["infusions"],
                          gang["reference_infusions"]), (True, 0, 1, 1))
        self.assertEqual(gang["end_offset_s"], 40.0)
        self.assertEqual(gang["gang_heat_after_exit_s"], 20.0)

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

        cb = self.protocol("CB", ((0, 20), (80, 90), (270, 280), (400, 410), (470, 490),
                                  (610, 620), (680, 690)), run_id="other")
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
            self.assertEqual(run.phase, "cancelled")
            self.assertTrue(runtime.session.operation_enabled)
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

    def test_session_end_evaluates_each_run_and_sends_the_result(self):
        async def exercise():
            clock = [START]
            runtime = self.runtime(clock)
            runtime.controller.set_temperature(80, START)
            await runtime.set_operation(True, actor=ActorRef(USER, "ha_context"))
            await self.sensor_gang(runtime, clock, calibrate=True)
            await self.settle(runtime)
            run = runtime.calibration_runs[0]
            first = runtime.notification_transport.sent[0]
            await runtime.calibration_reply(
                self.association(first, "reply", clock[0] + timedelta(seconds=10)))
            runtime.controller.finish_session(clock[0] + timedelta(seconds=20))
            clock[0] += timedelta(seconds=21)
            await runtime.tick()
            await self.settle(runtime)
            self.assertEqual(runtime.calibration_runs, ())
            self.assertEqual((run.phase, run.end_reason), ("cancelled", "session_ended"))
            final = runtime.notification_transport.sent[-1]
            self.assertTrue(final.payload["message"].startswith("Kalibrierung ausgewertet."))
            self.assertNotIn("data", final.payload)
            await runtime.close()

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
