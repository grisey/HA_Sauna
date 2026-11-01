"""Aufzeichnungen über die Laufzeit mit eingebauter Erkennung abspielen.

Aufruf:
    python tools/recognition_replay.py --data DIR [--params PARAMS.json] --out OUT.json R01 R04 ...

Die Messwerte erreichen die Erkennung der Laufzeit (``runtime.detector``) wie
im Betrieb; Gangmodell, Steuerung und Nachlauf bleiben unverändert.  Die
Schützrückmeldung folgt dem Schaltbefehl.  Ohne ``--presence`` gilt das
Proxyverfahren (Erkennung aus Temperatur und Feuchte); mit ``--presence``
erreichen aufgezeichnete Präsenzmeldungen (Rolle ``presence``) die Laufzeit
als Präsenzsensor, sofern die Aufzeichnung ihn verwendet.  Mit
``--heater recorded`` folgt die Schützrückmeldung dem aufgezeichneten
Schützzustand (Rolle ``heater``) statt der Heizanforderung.  Mit
``--heater split`` erhält die Erkennung den aufgezeichneten Schützzustand, der
zu den gemessenen Kurven gehört, und die Steuerung eine Schützrückmeldung, die
ihrem Schaltbefehl folgt wie im Automatikbetrieb.  Die Rückmeldung erreicht die
Steuerung in jedem Takt über dieselben Eingänge wie bei
``HADevice.report_received_feedback``.  Ohne Ofenleistung und unabhängigen
Heiznachweis ist ihre Heizquelle die Schalterrückmeldung des Heizschützes
(``contactor``, Rolle ``heater``).  Aufzeichnungen mit diesen optionalen
Rückmeldungen weist das Werkzeug ab, weil dieser Rückmeldeweg nicht nachgebildet
ist.  Ergebnis je Aufzeichnung: erkannte Ereignisse, gebuchte und abgelehnte
Ereignisse, Gänge.

Zusätzlich, nur beobachtet (Erkennung, Gangmodell und Steuerung verlaufen wie
ohne Beobachtung):

- ``control``: jede Entscheidung der Steuerung, bei der sich Heizanforderung
  (``demand``) oder Schaltbefehl (``command``) samt Grund, Kühlung, Phase,
  Gangzustand, Schützrückmeldung oder gebuchter Heizzustand ändert, mit
  Zeitpunkt.  Kühlung: ``pending`` bis zur ersten Schützrückmeldung AUS,
  ``running`` während die Kühlzeit zählt, ``suspended`` bei erneut gemeldetem
  Schütz EIN.
- ``heating``: Heizquelle und die von der Steuerung gebuchten Heizintervalle
  (Heizzeitbuchung aus der Rückmeldung).  Nur diese Intervalle ergeben
  Heizdauern (``heating_s``); Heizanforderung und Befehl stehen getrennt
  (``gang_demand_s``, ``command_s``).
- ``provisional``: jeder vorläufig erkannte Gang mit Beginn, Erkennung,
  Ausgang (bestätigt, zurückgenommen, beendet, offen), Zeitpunkt und Grund des
  Ausgangs sowie Heizdauer, Ganganforderung und Befehl EIN bis dahin.
- ``infusion_filter``: Trennungen der Güsse mit Grund (Türöffnung, Dampfende,
  abgeflachtes Abklingen), jeder Guss mit den Trennungen seit dem vorigen und
  die Abklingprüfung je Messschritt.  Deren Verhältnis ist wie in
  ``ClimateRecognizer._watch_decay`` nachgerechnet.

Zeiten in ``detections`` und ``infusion_filter`` sind Sekunden ab Beginn der
Zeitachse, alle übrigen ISO-Zeitpunkte.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import math
import sys
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from custom_components.ha_sauna.bindings import Bindings  # noqa: E402
from custom_components.ha_sauna.core.input_handoff import (
    ContactorFeedbackInput,
    HeatingFeedbackInput,
    InputTiming,
    PowerFeedbackInput,
)
from custom_components.ha_sauna.core.models import Measurement, Position, Quantity  # noqa: E402
from custom_components.ha_sauna.core.parameters import Parameters  # noqa: E402
from custom_components.ha_sauna.core.presence import binary_presence  # noqa: E402
from custom_components.ha_sauna.core.recognition_model import DECAY, INFUSION, MODES
from custom_components.ha_sauna.core.timeline import Kind
from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime  # noqa: E402

BINDINGS = {
    "upper_temperature": "sensor.oben_temperatur", "upper_humidity": "sensor.oben_feuchte",
    "lower_temperature": "sensor.unten_temperatur", "lower_humidity": "sensor.unten_feuchte",
    "heater": "switch.ofen", "light": "light.sauna", "control_input": "event.taster",
}
ROLES = {
    "upper_temperature": (Position.UPPER, Quantity.TEMPERATURE),
    "upper_humidity": (Position.UPPER, Quantity.HUMIDITY),
    "lower_temperature": (Position.LOWER, Quantity.TEMPERATURE),
    "lower_humidity": (Position.LOWER, Quantity.HUMIDITY),
}
ORIGIN = datetime(2000, 1, 1, 12, tzinfo=UTC)


def iso(at):
    return at.isoformat() if at else None


PRESENCE = "binary_sensor.praesenz"
# events that withdraw a provisional gang
RETRACTION = {Kind.VENTILATION: "ventilation", Kind.CONFIRMATION_EXPIRED: "expired",
              Kind.PRESENCE_ENTRY: "replaced"}
DECAY_COLUMNS = ("t", "state", "p_infusion", "p_decay", "joining", "slope", "steepest", "check", "ratio")
CONTROL_COLUMNS = ("demand", "demand_reason", "command", "command_reason", "cooling", "phase", "gang",
                   "contactor", "heating")
OPTIONAL_HEATING = ("heater_power", "heater_feedback")   # power and independent heating feedback


def seconds(at):
    return None if at is None else round((at - ORIGIN).total_seconds(), 3)


def control_state(c):
    """Heizanforderung und Schaltbefehl mit Grund, Kühlung, Phase, Gangzustand, Rückmeldung und gebuchter
    Heizzustand nach einer Entscheidung (``CONTROL_COLUMNS``)."""
    session, demand, command = c.session, c.automatic_decision, c.last_decision
    after_run = session.after_run if session else None
    gang = session.timeline.active if session else None
    cooling = (None if after_run is None else "pending" if after_run.pending_start
               else "suspended" if after_run.ends_at is None else "running")
    return (bool(demand and demand.heat), demand.reason if demand else None,
            bool(command and command.heat), command.reason if command else None, cooling, c.phase,
            gang.confirmation.value if gang else None, c.contactor,
            session.heating.reported_heating if session else None)


def report_feedback(c, contactor, received_at, at):
    """Schalterrückmeldung des Heizschützes über die Eingänge von ``HADevice.report_received_feedback``.

    Ohne Ofenleistung und unabhängigen Heiznachweis wählt die Integration die Schalterrückmeldung als
    Heizquelle (``HADevice.observe_heating``: ``contactor``); die Steuerung bucht daraus die Heizzeit.
    """
    timing = InputTiming(effective_at=received_at, received_at=received_at, first_usable_at=None, evaluated_at=at)
    c.consume_contactor_feedback_input(ContactorFeedbackInput(contactor=contactor, timing=timing))
    c.report_fallback_heating(contactor, at)
    c.consume_power_feedback_input(PowerFeedbackInput(power_w=None, valid_until=None, timing=InputTiming(
        effective_at=None, received_at=None, first_usable_at=None, evaluated_at=at)))
    c.consume_heating_feedback_input(HeatingFeedbackInput(heating=contactor, timing=timing))


def control_seconds(control, until, start, end, wanted):
    """Sekunden zwischen ``start`` und ``end``, in denen die Entscheidung ``wanted`` erfüllt."""
    total = 0.0
    for row, stop in zip(control, [r[0] for r in control[1:]] + [until]):
        lo, hi = max(row[0], start), min(stop, end)
        if hi > lo and wanted(row):
            total += (hi - lo).total_seconds()
    return round(total, 1)


def heating_seconds(intervals, until, start, end):
    """Sekunden zwischen ``start`` und ``end`` in gebuchten Heizintervallen."""
    total = 0.0
    for begin, stop in intervals:
        lo, hi = max(begin, start), min(stop or until, end)
        if hi > lo:
            total += (hi - lo).total_seconds()
    return round(total, 1)


def gang_demand(row):
    return row[2] == "gang_heat_demand"


def command_on(row):
    return row[3]


def observe_infusion_filter(rec):
    """Trennungen der Güsse mit Grund und die Abklingprüfung je Messschritt, nur lesend."""
    log = {"separations": [], "decay": []}
    source = [None]

    class Separations(deque):
        def append(self, step):
            reason, at = source[0] or (None, None)
            log["separations"].append((rec._index_time(step), at, reason))
            super().append(step)

    rec._separations = Separations(rec._separations, rec._separations.maxlen)

    def separating(method, reason):
        def observed(at, *args):
            source[0] = (reason, at)
            try:
                return method(at, *args)
            finally:
                source[0] = None
        return observed

    rec._confirm_opening = separating(rec._confirm_opening, "door_open")
    rec._decide_infusion = separating(rec._decide_infusion, "steam_end")
    watch_decay = rec._watch_decay

    def watched(mu, at):
        top = int(np.argmax(mu))
        pour = rec._pour
        joining = pour is not None and not any(k > pour for k in rec._separations)
        if top in (INFUSION, DECAY) or joining:
            slope, var = rec._model.humidity_decay_slope()
            steepest = rec._steepest
            check, ratio = ("rise" if top == INFUSION else None), None
            if top == DECAY and joining:
                if steepest is None or slope < steepest[0]:
                    check = "steepest"
                else:
                    z = (slope - steepest[0]) / math.sqrt(max(var + steepest[1], 1e-30))
                    less_steep = 0.5 * math.erfc(-z / math.sqrt(2))
                    check, ratio = "check", less_steep / max(1 - less_steep, 1e-300)
            log["decay"].append([seconds(at), MODES[top], round(float(mu[INFUSION]), 4),
                                 round(float(mu[DECAY]), 4), joining, round(slope, 5),
                                 None if steepest is None else round(steepest[0], 5), check,
                                 None if ratio is None else round(ratio, 1)])
        source[0] = ("decay_flattened", at)
        try:
            return watch_decay(mu, at)
        finally:
            source[0] = None

    rec._watch_decay = watched
    return log


def infusion_filter(rec, log):
    """Filterzustand einer Erkennung: Trennungen, Güsse mit den Trennungen seit dem vorigen Guss."""
    separations = log["separations"]
    pours, previous = [], None
    for d in rec.decisions:
        if d["event"] not in ("infusion", "infusion_pour"):
            continue
        t, decided = datetime.fromisoformat(d["effective_at"]), datetime.fromisoformat(d["decided_at"])
        between = None if previous is None else [
            {"t": seconds(s), "reason": reason} for s, at, reason in separations
            if previous < s <= t and (at is None or at <= decided)]
        pours.append({"t": seconds(t), "decided": seconds(decided), "joined": d["event"] == "infusion_pour",
                      "previous": seconds(previous), "separations": between})
        previous = t
    return {"separations": [{"t": seconds(s), "decided": seconds(at), "reason": reason}
                            for s, at, reason in separations],
            "pours": pours, "decay": log["decay"]}


async def replay(case_dir: Path, parameters: dict | None, *, presence: bool = False, heater: str = "demand"):
    case = json.loads((case_dir / "case.json").read_text())
    rows = [json.loads(line) for line in gzip.open(case_dir / "samples.jsonl.gz")]
    rows.sort(key=lambda r: r["t"])
    direct = (presence and (case.get("configuration") or {}).get("presence_source") == "ha_presence"
              and any(r["role"] == "presence" for r in rows))
    recorded_heater = [(r["t"], r["value"]) for r in rows if r["role"] == "heater" and isinstance(r["value"], bool)]
    if heater in ("recorded", "split") and not recorded_heater:
        raise ValueError(f"{case_dir.name}: keine aufgezeichnete Schützrückmeldung")
    optional = sorted({r["role"] for r in rows} & set(OPTIONAL_HEATING))
    if optional:
        raise ValueError(f"{case_dir.name}: {', '.join(optional)} aufgezeichnet; dieser Rückmeldeweg ist im "
                         "Replay nicht nachgebildet")
    heater_state = [None]
    heater_at = [None]
    feedback = [None, None]          # contactor feedback reported to the controller and its receipt
    # split: the recognition receives the recorded contactor exactly as the runtime forwards it with
    # "recorded" (the feedback reported after the previous cycle); the controller receives its demand
    recognition_heater = [None]

    def recorded_heating(report):
        def reported(at, on):
            return report(at, recognition_heater[0])
        return reported
    actions = case.get("actions") or case.get("bound_actions") or []
    switches = sorted((a["t"], a["name"] == "start") for a in actions if a["name"] in ("start", "stop"))
    if not switches:
        switches = [(rows[0]["t"], True)]
    now = [ORIGIN]
    bindings = dict(BINDINGS) | ({"presence": PRESENCE} if direct else {})
    runtime = SaunaRuntime(Configuration(
        Bindings(bindings), Parameters({"target_temperature_c": 90.0}),
        presence_source="ha_presence" if direct else "proxy", control_input_mode="button",
        control_mode="automatic",
    ), clock=lambda: now[0])
    if parameters is not None:
        runtime.recognition_parameters = parameters
    c = runtime.controller
    rejected = []
    process = runtime._process_event

    def recorded(event, **kwargs):
        try:
            return process(event, **kwargs)
        except ValueError as error:
            rejected.append((event.kind.value, iso(event.effective_at), str(error)))
            raise

    runtime._process_event = recorded
    control = []                     # (time, *CONTROL_COLUMNS) at every change
    evaluate = c._evaluate

    def evaluated(at, **kwargs):
        decision = evaluate(at, **kwargs)
        state = control_state(c)
        if not control or control[-1][1:] != state:
            control.append((at, *state))
        return decision

    c._evaluate = evaluated
    retractions = {}                 # gang id → (time, reason) of its withdrawal
    seen = {}                        # session id → processed events and withdrawn gangs already seen

    def watch_timeline():
        if c.session is None:
            return
        timeline = c.session.timeline
        events, withdrawn = seen.get(c.session.session_id, (0, 0))
        causes = [e for e in timeline.processed[events:] if e.kind in RETRACTION]
        for gang in timeline.retracted[withdrawn:]:
            retractions[gang.gang_id] = ((causes[0].booking_at, RETRACTION[causes[0].kind]) if len(causes) == 1
                                         else (None, "ambiguous:" + "+".join(RETRACTION[e.kind] for e in causes)))
        seen[c.session.session_id] = (len(timeline.processed), len(timeline.retracted))

    def observing(method):
        def observed(*args, **kwargs):
            try:
                return method(*args, **kwargs)
            finally:
                watch_timeline()
        return observed

    c.process = observing(c.process)
    c.consume_deadline = observing(c.consume_deadline)
    filters = {}                     # recognizer id → observed infusion filter
    sync_detector = runtime._sync_detector

    def synced():
        # a new recognizer is observed before the runtime reports any heating or measurement to it
        sync_detector()
        detector = runtime.detector
        if detector is not None and id(detector) not in filters:
            filters[id(detector)] = observe_infusion_filter(detector)
            if heater == "split":
                detector.report_heating = recorded_heating(detector.report_heating)

    runtime._sync_detector = synced
    finished = []
    runtime.recognition_finished = lambda session_id, recognizer: finished.append((session_id, recognizer))
    latest: dict = {}
    fed_to = [None]
    upper_t = None
    index = 0
    second = int(rows[0]["t"]) + 1
    end = int(rows[-1]["t"]) + 1
    sessions = {}
    while second <= end:
        now[0] = ORIGIN + timedelta(seconds=second)
        reports = []
        while index < len(rows) and rows[index]["t"] <= second:
            r = rows[index]
            index += 1
            if r["role"] == "heater":
                if isinstance(r["value"], bool):
                    heater_state[0] = r["value"]
                    heater_at[0] = ORIGIN + timedelta(seconds=r["t"])
                continue
            if direct and r["role"] == "presence" and isinstance(r["value"], bool):
                at = ORIGIN + timedelta(seconds=r["t"])
                reports.append(binary_presence(PRESENCE, "on" if r["value"] else "off", at, at))
                continue
            role = ROLES.get(r["role"])
            if role is None or isinstance(r["value"], bool) or not isinstance(r["value"], (int, float)):
                continue
            at = ORIGIN + timedelta(seconds=r["t"])
            m = Measurement(role[0], role[1], float(r["value"]), str(r["value"]), BINDINGS[r["role"]], at)
            latest[r["role"]] = m
            if r["role"] == "upper_temperature":
                upper_t = float(r["value"])
            if runtime.detector is not None and fed_to[0] is runtime.detector:
                runtime.detector.accept(m)
        if upper_t is not None:
            c.set_temperature(upper_t, now[0], valid_until=now[0] + timedelta(seconds=30))
        while switches and switches[0][0] <= second and upper_t is not None:
            _, on = switches.pop(0)
            enabled = bool(c.session and c.session.operation_enabled)
            if on and not enabled:
                c.set_operation(True, now[0], session_id=f"replay-{case_dir.name}-{second}")
            elif not on and enabled:
                c.set_operation(False, now[0])
        for report in reports:
            await runtime.accept_presence(report)
        await runtime.tick()
        if runtime.detector is not None and fed_to[0] is not runtime.detector:
            # a new session's recognizer starts with the current values (as the device snapshot does)
            fed_to[0] = runtime.detector
            for m in latest.values():
                runtime.detector.accept(m)
        if c.session is not None:
            # every cycle, as HADevice.refresh reports the feedback; recorded or following the command
            if heater == "recorded":
                contactor, received_at = heater_state[0], heater_at[0]
            else:
                contactor = bool(c.last_decision and c.last_decision.heat)
                received_at = now[0] if contactor is not feedback[0] or feedback[1] is None else feedback[1]
            feedback[:] = [contactor, received_at]
            recognition_heater[0] = heater_state[0]
            report_feedback(c, contactor, received_at, now[0])
            sessions[c.session.session_id] = c.session
        watch_timeline()
        second += 1
    until = now[0]
    booked = sorted((i.started_at, i.ended_at) for session in sessions.values() for i in session.heating.intervals)
    recognizers = [rec for _, rec in finished] + ([runtime.detector] if runtime.detector else [])
    gangs, retracted, processed, provisional = [], [], [], []
    for session in sessions.values():
        timeline = session.timeline
        for g in (*timeline.completed, *([timeline.active] if timeline.active else [])):
            gangs.append({"start": iso(g.started_at), "detected": iso(g.detected_at), "end": iso(g.ended_at),
                          "end_reason": g.end_reason, "confirmation": g.confirmation.value,
                          "infusions": len(g.infusion_events), "recognition": g.recognition_kind.value,
                          "confirmed": iso(g.confirmed_at),
                          "infusion_times": [iso(e.effective_at) for e in g.infusion_events],
                          "heating_s": heating_seconds(booked, until, g.detected_at, g.ended_at or until),
                          "gang_demand_s": control_seconds(control, until, g.detected_at, g.ended_at or until,
                                                           gang_demand),
                          "command_s": control_seconds(control, until, g.detected_at, g.ended_at or until,
                                                       command_on)})
        for g in timeline.retracted:
            at, reason = retractions.get(g.gang_id, (None, None))
            retracted.append({"start": iso(g.started_at), "detected": iso(g.detected_at), "retracted": iso(at),
                              "reason": reason})
        withdrawn = {g.gang_id for g in timeline.retracted}
        for g in (*timeline.completed, *timeline.retracted, *([timeline.active] if timeline.active else [])):
            if g.confirmed_at is not None and g.confirmed_at <= g.detected_at:
                continue                 # confirmed when recognized: never provisional
            if g.gang_id in withdrawn:
                outcome, (at, reason) = "retracted", retractions.get(g.gang_id, (None, None))
            elif g.confirmed_at is not None:
                outcome, at, reason = "confirmed", g.confirmed_at, g.infusion_events[0].kind.value
            elif g.ended_at is not None:
                outcome, at, reason = "ended", g.ended_at, g.end_reason
            else:
                outcome, at, reason = "open", None, None
            provisional.append({"start": iso(g.started_at), "detected": iso(g.detected_at),
                                "recognition": g.recognition_kind.value, "outcome": outcome, "at": iso(at),
                                "reason": reason,
                                "heating_s": heating_seconds(booked, until, g.detected_at, at or until),
                                "gang_demand_s": control_seconds(control, until, g.detected_at, at or until,
                                                                 gang_demand),
                                "command_s": control_seconds(control, until, g.detected_at, at or until, command_on)})
        processed += [(e.kind.value, iso(e.effective_at), iso(e.booking_at)) for e in timeline.processed
                      if e.event_id.startswith("recognition:")]
    provisional.sort(key=lambda g: g["detected"])
    detections = [(d.kind.value, (d.effective_at - ORIGIN).total_seconds(), (d.detected_at - ORIGIN).total_seconds())
                  for rec in recognizers for d in rec.detections]
    decisions = [d for rec in recognizers for d in rec.decisions]
    filtered = [infusion_filter(rec, filters[id(rec)]) for rec in recognizers if id(rec) in filters]
    return {"recording": case_dir.name, "sessions": len(sessions), "gangs": gangs, "retracted": retracted,
            "detections": detections, "processed": processed, "rejected": rejected, "decisions": decisions,
            "presence_source": "ha_presence" if direct else "proxy", "heater": heater, "until": iso(until),
            "heating": {"source": "contactor", "role": "heater",
                        "follows": "recording" if heater == "recorded" else "command",
                        "intervals": [[iso(a), iso(b)] for a, b in booked]},
            "control": [{"at": iso(row[0]), **dict(zip(CONTROL_COLUMNS, row[1:]))} for row in control],
            "provisional": provisional,
            "infusion_filter": {"separations": [s for f in filtered for s in f["separations"]],
                                "pours": [p for f in filtered for p in f["pours"]],
                                "decay_columns": DECAY_COLUMNS,
                                "decay": [row for f in filtered for row in f["decay"]]}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--params")
    ap.add_argument("--out", required=True)
    ap.add_argument("--presence", action="store_true",
                    help="aufgezeichnete Präsenzmeldungen verwenden, wenn die Aufzeichnung den Sensor nutzt")
    ap.add_argument("--heater", choices=("demand", "recorded", "split"), default="demand",
                    help="Schützrückmeldung aus der Heizanforderung, aus der Aufzeichnung oder getrennt: "
                         "Erkennung aus der Aufzeichnung, Steuerung aus der Heizanforderung")
    ap.add_argument("recordings", nargs="+")
    args = ap.parse_args()
    parameters = json.loads(Path(args.params).read_text()) if args.params else None
    out = {}
    for name in args.recordings:
        out[name] = asyncio.run(replay(Path(args.data) / name, parameters, presence=args.presence,
                                       heater=args.heater))
        g = out[name]["gangs"]
        print(name, "Gänge", len([x for x in g if x["confirmation"] == "confirmed"]),
              "verworfen", len(out[name]["retracted"]), "abgelehnt", len(out[name]["rejected"]), flush=True)
    Path(args.out).write_text(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
