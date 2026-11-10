"""BN Benachrichtigungen der Sauna (Nutzer 10.11.2026): Anlässe T, Z1, Z2, B1, B2, G, U, E, S.

Der Ablauf liest den Zustand nur; Antworten mit Wirkung gehen über die
vorhandenen Bedienwege (Gang beenden, Betrieb aus) und nur mit der
Steuerberechtigung des Antwortenden. Laufzeitfälle verwenden die produktiven
Methoden mit einem synthetischen letzten Transport.
"""

import asyncio
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from math import exp
from zoneinfo import ZoneInfo

from test_presence_regressions import START, _runtime

from custom_components.ha_sauna.bindings import Bindings
from custom_components.ha_sauna.core import notification_rules as rules
from custom_components.ha_sauna.core import ready_forecast
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

T0 = datetime(2026, 11, 10, 17, tzinfo=UTC)
ZONE = ZoneInfo("Europe/Berlin")
USER, OTHER = "user-1", "user-2"


def at(seconds):
    return T0 + timedelta(seconds=seconds)


def facts(seconds, **values):
    base = {"now": at(seconds), "session_id": "s1", "session_started_at": T0,
            "operation_enabled": True}
    return rules.Facts(**{**base, **values})


class SettingsTests(unittest.TestCase):
    def test_defaults_roundtrip_and_validation(self):
        settings = rules.NotificationSettings()
        self.assertEqual(settings.timer_runtime_minutes, 240)
        self.assertEqual(settings.timer_lead_minutes, 15)
        self.assertEqual(settings.timer_repeat_minutes, 10)
        self.assertEqual(settings.overtemperature_c, 105)
        data = settings.as_dict()
        self.assertTrue(all(data["occasions"].values()))
        self.assertEqual(rules.NotificationSettings.from_dict(data), settings)
        changed = rules.replace_settings(settings, {"occasions": {"idle": False},
                                                    "timer_lead_minutes": 20})
        self.assertFalse(changed.enabled("idle"))
        self.assertEqual(changed.timer_lead_minutes, 20)
        for invalid in ({"timer_lead_minutes": 240}, {"overtemperature_c": 0},
                        {"occasions": {"unknown": True}}, {"occasions": {"idle": 1}},
                        {"other": 1}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                rules.replace_settings(settings, invalid)

    def test_description_names_every_occasion_and_value(self):
        described = rules.describe()
        self.assertEqual([item["id"] for item in described["occasions"]], list(rules.OCCASIONS))
        self.assertEqual([item["key"] for item in described["values"]], list(rules.VALUES))


class OccasionTests(unittest.TestCase):
    def setUp(self):
        # The timer occasions have their own case; here they would repeat every 10 min.
        self.notifier = rules.Notifier(rules.NotificationSettings(
            frozenset({"timer_start", "timer_expiry"})), zone=ZONE)

    def advance(self, value):
        messages, ended = self.notifier.advance(value)
        return messages, ended

    def test_timer_start_repeats_until_answered_and_counts_oven_time(self):
        self.notifier = rules.Notifier(zone=ZONE)
        messages, _ = self.advance(facts(0))
        self.assertEqual([m.occasion for m in messages], ["timer_start"])
        self.assertEqual(messages[0].actions, (rules.TIMER_SET,))
        self.assertEqual(messages[0].audience, "timer")
        self.assertEqual(self.advance(facts(599))[0], [])
        repeat, _ = self.advance(facts(600))
        self.assertEqual([m.occasion for m in repeat], ["timer_start"])
        # BN-2: no repetition while a gang acts; it follows after the gang.
        gang = rules.GangFact("g1", at(700), 1, True)
        self.assertEqual(self.advance(facts(1300, gang=gang))[0], [])
        self.assertEqual([m.occasion for m in self.advance(facts(1301))[0]], ["timer_start"])
        self.assertEqual(self.notifier.reply(repeat[0].seq, rules.TIMER_SET, at(1310), USER,
                                             current_session="s1"), "confirmed")
        self.assertEqual(self.advance(facts(4000))[0], [])
        # Only booked heating counts: 200 min of heating since the reply.
        heating = ((at(1310), at(1310 + 200 * 60)), (at(1310 + 300 * 60), None))
        self.assertEqual(self.advance(facts(1310 + 300 * 60 + 1, heating=heating))[0], [])
        late = facts(1310 + 325 * 60, heating=heating)
        expiry, _ = self.advance(late)
        self.assertEqual([m.text for m in expiry], ["Zeitschaltuhr läuft in etwa 15 min ab"])
        self.assertEqual(self.advance(facts(1310 + 330 * 60, heating=heating))[0], [])
        self.assertEqual(self.notifier.reply(expiry[0].seq, rules.TIMER_SET, late.now, OTHER,
                                             current_session="s1"), "confirmed")
        self.assertEqual(self.notifier.timer["confirmed_by"], OTHER)
        self.assertEqual(self.notifier.reply(expiry[0].seq, rules.TIMER_SET, late.now, OTHER,
                                             current_session="s1"), "duplicate")
        self.assertEqual(self.notifier.reply(expiry[0].seq, rules.TIMER_SET, late.now, OTHER,
                                             current_session="s2"), "late")

    def test_without_confirmation_there_is_no_count_and_no_reminder(self):
        self.notifier.configure(rules.replace_settings(
            self.notifier.settings, {"occasions": {"timer_expiry": True}}))
        heating = ((T0, None),)
        self.assertEqual(self.advance(facts(0, heating=heating))[0], [])
        self.assertEqual(self.advance(facts(300 * 60, heating=heating))[0], [])

    def test_ready_messages_at_each_transition(self):
        self.advance(facts(0))
        first, _ = self.advance(facts(10, ready_marks=((at(10), True),), target_c=85))
        self.assertEqual([m.text for m in first], ["Sauna bereit, 85 °C"])
        marks = ((at(10), True), (at(100), False), (at(900), True))
        second, _ = self.advance(facts(900, ready_marks=marks, target_c=88.5, gang_count=1))
        self.assertEqual([m.text for m in second], ["Bereit für Gang 2, 88,5 °C"])
        self.assertEqual(second[0].audience, "participants")
        self.assertEqual(self.advance(facts(901, ready_marks=marks, target_c=88.5,
                                            gang_count=1))[0], [])

    def test_gang_end_question_after_door_and_absence(self):
        self.advance(facts(0))
        gang = rules.GangFact("g1", at(100), 1, False)
        doors = (("door_open", at(90)), ("door_close", at(100)), ("door_open", at(700)),
                 ("door_close", at(708)))
        # Absence while the exit door is still open: the gang model decides at the close.
        self.assertEqual(self.advance(facts(705, gang=gang, door_open=True, door_events=doors[:3],
                                            presence=("absent", at(704))))[0], [])
        messages, _ = self.advance(facts(720, gang=gang, door_events=doors,
                                          presence=("absent", at(704))))
        self.assertEqual([m.text for m in messages],
                         ["Gang 1 läuft laut Sauna noch – bist du draußen?"])
        self.assertEqual((messages[0].actions, messages[0].gang_id), ((rules.END_GANG,), "g1"))
        self.assertEqual(self.advance(facts(730, gang=gang, door_events=doors,
                                            presence=("absent", at(704))))[0], [])
        self.assertEqual(self.notifier.reply(messages[0].seq, rules.END_GANG, at(740), USER,
                                             current_session="s1"), "effect")

    def test_absence_without_door_in_the_gang_asks_nothing(self):
        self.advance(facts(0))
        gang = rules.GangFact("g1", at(100), 1, False)
        doors = (("door_open", at(90)), ("door_close", at(100)))
        self.assertEqual(self.advance(facts(720, gang=gang, door_events=doors,
                                            presence=("absent", at(704))))[0], [])

    def test_idle_after_two_upper_switch_offs_without_gang_or_presence(self):
        self.notifier.configure(rules.replace_settings(
            self.notifier.settings, {"occasions": {"ready": False}}))
        self.advance(facts(0))
        marks = ((at(100), True),)
        common = {"ready_marks": marks, "target_c": 85}
        self.assertEqual(self.advance(facts(1000, switch_offs=(at(200),), **common))[0], [])
        messages, _ = self.advance(facts(2000, switch_offs=(at(200), at(1500)), **common))
        self.assertEqual([m.text for m in messages], ["Sauna läuft seit 18:01 ohne Nutzung"])
        self.assertEqual(messages[0].actions, (rules.SWITCH_OFF,))
        self.assertEqual(self.advance(facts(3000, switch_offs=(at(200), at(1500), at(2800)),
                                            **common))[0], [])

    def test_presence_or_a_gang_end_restart_the_idle_count(self):
        self.notifier.configure(rules.replace_settings(
            self.notifier.settings, {"occasions": {"ready": False}}))
        self.advance(facts(0))
        marks = ((at(100), True),)
        offs = (at(200), at(1500))
        self.assertEqual(self.advance(facts(2000, ready_marks=marks, switch_offs=offs,
                                            last_present_at=at(300)))[0], [])
        self.assertEqual(self.advance(facts(2000, ready_marks=marks, switch_offs=offs,
                                            presence=("present", at(50))))[0], [])
        self.assertEqual(self.advance(facts(2000, ready_marks=marks, switch_offs=offs,
                                            last_gang_end=at(1000)))[0], [])

    def test_safety_messages_once_per_episode_also_without_session(self):
        notifier = self.notifier
        no_session = {"session_id": None, "operation_enabled": False}
        hot = facts(0, upper_temperature_c=105.2, contactor=True, **no_session)
        messages, _ = notifier.advance(hot)
        self.assertEqual([m.text for m in messages],
                         ["Achtung: Übertemperatur 105,2 °C (Grenze 105 °C)"])
        self.assertEqual(messages[0].audience, "assigned")
        self.assertEqual(notifier.advance(facts(1, upper_temperature_c=104.9, contactor=True,
                                                **no_session))[0], [])
        self.assertEqual(notifier.advance(facts(2, upper_temperature_c=105.1, contactor=True,
                                                **no_session))[0], [])
        notifier.advance(facts(3, upper_temperature_c=100, contactor=False, **no_session))
        self.assertEqual(len(notifier.advance(facts(4, upper_temperature_c=106, contactor=True,
                                                    **no_session))[0]), 1)
        against, _ = notifier.advance(facts(5, heating_against_off=True, **no_session))
        self.assertEqual([m.text for m in against],
                         ["Achtung: Ofen meldet „an“, obwohl er aus sein soll"])
        failed, _ = notifier.advance(facts(6, upper_failed=True, contactor=True, **no_session))
        self.assertEqual([m.text for m in failed],
                         ["Achtung: obere Temperaturmessung ausgefallen, während der Ofen heizt"])
        self.assertEqual(notifier.advance(facts(7, upper_failed=True, contactor=False,
                                                **no_session))[0], [])

    def test_disabled_occasion_sends_nothing(self):
        self.notifier.configure(rules.replace_settings(
            self.notifier.settings, {"occasions": {"ready": False}}))
        self.assertEqual(self.advance(facts(0))[0], [])
        self.assertEqual(self.advance(facts(10, ready_marks=((at(10), True),)))[0], [])

    def test_session_end_is_reported_once(self):
        self.advance(facts(0))
        messages, ended = self.advance(facts(10, session_id=None, operation_enabled=False))
        self.assertEqual((messages, ended), ([], "s1"))
        figures = {"gang_count": {"display": 3}, "infusions_total": {"display": 11},
                   "sauna_time_seconds": {"display": 46.4}, "energy_kwh": {"display": 12.4},
                   "cost_eur": {"display": 3.47}}
        text = rules.summary(figures, heater_off=True)
        self.assertEqual(text, "Sauna aus · Ofen aus bestätigt\n3 Gänge, 11 Aufgüsse, "
                               "Saunazeit 46,4 min, 12,4 kWh, 3,47 €")
        self.assertEqual(rules.summary(None, heater_off=None), "Sauna aus · Ofenrückmeldung fehlt")
        message = self.notifier.session_end(at(11), "s1", text)
        self.assertEqual(message.audience, "participants")

    def test_upper_switch_offs_are_heat_to_temperature_reached(self):
        decisions = [(at(0), True, "below_target"), (at(10), False, "temperature_reached"),
                     (at(20), False, "thermostat_cooldown"), (at(30), True, "below_target"),
                     (at(40), False, "operation_off"), (at(50), True, "hysteresis_band"),
                     (at(60), False, "temperature_reached")]
        self.assertEqual(rules.upper_switch_offs(decisions), (at(10), at(60)))


def recovery_then_line(seconds, *, start=80.0, rise=0.012, recovery=3.0, tau=60.0):
    """Synthetic pause: recovery with a time constant, then the heater's straight rise."""
    return [start + rise * t + recovery * (1 - exp(-t / tau)) for t in range(seconds)]


class ReadyForecastTests(unittest.TestCase):
    def test_recovery_still_running_is_not_linear(self):
        values = recovery_then_line(120)
        self.assertIsNone(ready_forecast.forecast(range(120), values, 90.0))

    def test_linear_rise_after_the_recovery_gives_the_ready_time(self):
        values = recovery_then_line(900)
        noise = [0.004 * ((i * 7919) % 11 - 5) / 5 for i in range(900)]
        decided = None
        for end in range(200, 900, 10):
            decided = ready_forecast.forecast(range(end), [v + n for v, n in
                                                           zip(values[:end], noise[:end])], 90.0)
            if decided is not None:
                break
        self.assertIsNotNone(decided)
        expected = next(t for t in range(5000)
                        if 80 + 0.012 * t + 3 * (1 - exp(-t / 60)) >= 90)
        self.assertLess(abs(decided.ready_at - expected), 60)
        self.assertGreater(decided.linear_from, 60)
        self.assertAlmostEqual(decided.slope_c_per_s, 0.012, delta=0.002)

    def test_curved_approach_never_counts_as_linear(self):
        values = [80 + 10 * (1 - exp(-t / 900)) for t in range(1200)]
        self.assertIsNone(ready_forecast.forecast(range(1200), values, 95.0))

    def test_no_forecast_above_target_or_for_falling_values(self):
        self.assertIsNone(ready_forecast.forecast(range(100), [91.0] * 100, 90.0))
        self.assertIsNone(ready_forecast.forecast(range(600), [90 - 0.01 * t for t in range(600)],
                                                  95.0))


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


class Archive:
    def __init__(self):
        self.records = []

    def append(self, kind, at_, payload, session_id=None):
        self.records.append((kind, payload))

    def save_session(self, *args):
        pass

    failure = None
    revision = 0

    async def flush(self):
        pass

    async def close(self):
        pass

    def session_key_figures(self, session_id, **settings):
        return None


class RuntimeTests(unittest.TestCase):
    def runtime(self, clock, *, present=frozenset()):
        phone = RecipientRef("device", "phone-1", "home_assistant")
        notify = RecipientRef("entity", "notify.other", "home_assistant")
        configuration = _runtime(clock).configuration
        configuration = replace(
            configuration, presence_source="ha_presence",
            bindings=Bindings({**configuration.bindings.values,
                               "presence": "binary_sensor.presence"}),
            notification_assignments=(RecipientAssignment(USER, frozenset((phone,))),
                                      RecipientAssignment(OTHER, frozenset((notify,)))),
        )
        runtime = SaunaRuntime(configuration, clock=lambda: clock[0])
        runtime.notification_entry_id = "entry"
        runtime.notification_transport = Transport()
        runtime.notification_resolutions = (
            RecipientResolution(phone, ResolvedRecipient(
                phone, TargetIdentity("home_assistant", "phone-1"),
                RecipientCapability(send=CapabilityEvidence("t", "send"),
                                    reply=CapabilityEvidence("t", "reply")))),
            RecipientResolution(notify, ResolvedRecipient(
                notify, TargetIdentity("home_assistant", "notify.other"),
                RecipientCapability(send=CapabilityEvidence("t", "send")))),
        )
        runtime.enable_notifications(zone=ZONE)
        runtime.notification_presence = lambda targets: frozenset(
            target for target in targets if target.endpoint in present)
        runtime.archive = Archive()
        return runtime

    @staticmethod
    async def settle(runtime):
        for _ in range(5):
            while runtime._bn_tasks:
                await asyncio.gather(*tuple(runtime._bn_tasks))
            await asyncio.sleep(0)

    @staticmethod
    def answer(notification, kind, at_, user=USER):
        action = next(item["action"] for item in notification.payload["data"]["actions"]
                      if item["action"].endswith(kind.upper()))
        reply = ReplyEnvelope(None, None, None, None, None, ActorRef(user, "HA event context"),
                              {"action": action}, observed_at=at_,
                              reply_key=ReplyKey("home_assistant", action))
        return ReplyAssociation(ReplyDisposition.ACCEPTED, reply, notification, True)

    def test_test_message_goes_to_own_targets_and_shows_the_reply(self):
        async def exercise():
            clock = [START]
            runtime = self.runtime(clock)
            result = await runtime.send_test_notification(actor=ActorRef(USER, "ha"))
            await self.settle(runtime)
            sent = runtime.notification_transport.sent
            self.assertEqual(result["targets"], 1)
            self.assertEqual([n.target.endpoint for n in sent], ["phone-1"])
            self.assertEqual(sent[0].payload["message"], "Testnachricht der Sauna")
            self.assertEqual([a["title"] for a in sent[0].payload["data"]["actions"]],
                             ["Empfangen"])
            status = runtime.notification_status(USER)["test"]
            self.assertEqual((status["targets"], status["accepted"], status["replied_at"]),
                             (1, 1, None))
            clock[0] += timedelta(seconds=20)
            self.assertEqual(await runtime.notification_reply(
                self.answer(sent[0], rules.RECEIVED, clock[0]), permitted=False), "received")
            status = runtime.notification_status(USER)["test"]
            self.assertEqual((status["replied_at"], status["replied_by"]), (clock[0], USER))
            # Send-only target: message without native actions.
            await runtime.send_test_notification(actor=ActorRef(OTHER, "ha"))
            await self.settle(runtime)
            self.assertNotIn("data", sent[-1].payload)
            with self.assertRaisesRegex(ValueError, "kein Benachrichtigungsziel"):
                await runtime.send_test_notification(actor=ActorRef("nobody", "ha"))
            await runtime.close()

        asyncio.run(exercise())

    def test_timer_start_goes_to_the_starting_user_and_else_to_present_devices(self):
        async def exercise(actor, present, expected):
            clock = [START]
            runtime = self.runtime(clock, present=present)
            runtime.controller.set_temperature(60, START)
            await runtime.set_operation(True, actor=actor)
            await self.settle(runtime)
            sent = runtime.notification_transport.sent
            self.assertEqual(sorted(n.target.endpoint for n in sent), expected)
            self.assertTrue(all(n.payload["message"] == "Sauna gestartet – Zeitschaltuhr aufziehen"
                                for n in sent))
            recipients = [payload for kind, payload in runtime.archive.records
                          if kind == "notification" and payload["event"] == "recipients"]
            await runtime.close()
            return recipients[0]["basis"]

        self.assertEqual(asyncio.run(exercise(ActorRef(USER, "ha"), frozenset(), ["phone-1"])),
                         "startender Benutzer")
        self.assertEqual(asyncio.run(exercise(None, {"phone-1"}, ["phone-1"])),
                         "anwesende Geräte")
        self.assertEqual(asyncio.run(exercise(ActorRef(None, "ha"), frozenset(),
                                              ["notify.other", "phone-1"])),
                         "alle Zugeordneten (kein Gerät anwesend)")

    def test_timer_reply_is_stored_and_ends_the_repetition(self):
        async def exercise():
            clock = [START]
            runtime = self.runtime(clock)
            runtime.controller.set_temperature(60, START)
            await runtime.set_operation(True, actor=ActorRef(USER, "ha"))
            await self.settle(runtime)
            sent = runtime.notification_transport.sent
            clock[0] += timedelta(seconds=90)
            self.assertEqual(await runtime.notification_reply(
                self.answer(sent[0], rules.TIMER_SET, clock[0]), permitted=False), "confirmed")
            self.assertEqual(runtime.notifier.timer["confirmed_at"], clock[0])
            clock[0] += timedelta(minutes=30)
            await runtime.tick()
            await self.settle(runtime)
            self.assertEqual(len(sent), 1)
            replies = [payload for kind, payload in runtime.archive.records
                       if kind == "notification" and payload["event"] == "reply"]
            self.assertEqual((replies[0]["outcome_text"], replies[0]["user_id"]),
                             ("Zeitschaltuhr bestätigt", USER))
            await runtime.close()

        asyncio.run(exercise())

    async def gang(self, runtime, clock):
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
        await receive(Kind.INFUSION, 200, "recognition:water")
        # A short door in the gang, then a second infusion: the gang continues.
        await receive(Kind.DOOR_OPEN, 400, "recognition:bucket-open")
        await receive(Kind.DOOR_CLOSE, 410, "recognition:bucket-close")
        await receive(Kind.INFUSION, 420, "recognition:water-2")
        await presence("off", 600)
        return runtime.session.timeline.active

    def test_gang_end_reply_ends_the_gang_only_with_control_permission(self):
        async def exercise(permitted):
            clock = [START]
            runtime = self.runtime(clock)
            runtime.controller.set_temperature(80, START)
            await runtime.set_operation(True, actor=ActorRef(USER, "ha"))
            gang = await self.gang(runtime, clock)
            await self.settle(runtime)
            self.assertIsNotNone(gang)
            question = [n for n in runtime.notification_transport.sent
                        if n.payload["message"].startswith("Gang 1 läuft")]
            self.assertEqual(len(question), 1)
            clock[0] += timedelta(seconds=30)
            outcome = await runtime.notification_reply(
                self.answer(question[0], rules.END_GANG, clock[0]), permitted=permitted)
            active = runtime.session.timeline.active
            ended = runtime.session.timeline.completed[-1] if not active else None
            late = await runtime.notification_reply(
                self.answer(question[0], rules.END_GANG, clock[0]), permitted=permitted)
            await runtime.close()
            return outcome, active, ended, late

        outcome, active, ended, late = asyncio.run(exercise(True))
        self.assertEqual(outcome, "effect")
        self.assertIsNone(active)
        self.assertEqual(ended.end_reason, "manual")
        self.assertEqual(late, "late")
        outcome, active, ended, late = asyncio.run(exercise(False))
        self.assertEqual(outcome, "no_permission")
        self.assertIsNotNone(active)

    def test_switch_off_reply_turns_operation_off_once(self):
        async def exercise():
            clock = [START]
            runtime = self.runtime(clock)
            runtime.controller.set_temperature(60, START)
            await runtime.set_operation(True, actor=ActorRef(USER, "ha"))
            notifier = runtime.notifier
            message = notifier._emit("idle", clock[0], "Sauna läuft seit 18:00 ohne Nutzung",
                                     actions=(rules.SWITCH_OFF,),
                                     session_id=runtime.session.session_id)
            runtime._send_bn(message, runtime._user_targets(USER), "Teilnehmer")
            await self.settle(runtime)
            notification = runtime.notification_transport.sent[-1]
            outcome = await runtime.notification_reply(
                self.answer(notification, rules.SWITCH_OFF, clock[0], OTHER), permitted=True)
            self.assertEqual(outcome, "effect")
            self.assertFalse(runtime.session.operation_enabled)
            self.assertEqual(await runtime.notification_reply(
                self.answer(notification, rules.SWITCH_OFF, clock[0], OTHER), permitted=True),
                "late")
            replies = [payload for kind, payload in runtime.archive.records
                       if kind == "notification" and payload["event"] == "reply"]
            self.assertEqual([item["outcome"] for item in replies], ["effect", "late"])
            self.assertIn("bereits aus", replies[-1]["outcome_text"])
            await runtime.close()

        asyncio.run(exercise())

    def test_runtime_without_loaded_settings_sends_nothing(self):
        async def exercise():
            clock = [START]
            runtime = self.runtime(clock)
            runtime.notifier = None
            runtime.controller.set_temperature(60, START)
            await runtime.set_operation(True, actor=ActorRef(USER, "ha"))
            await self.settle(runtime)
            self.assertEqual(runtime.notification_transport.sent, [])
            await runtime.close()

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
