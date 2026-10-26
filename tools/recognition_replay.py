"""Aufzeichnungen über die Laufzeit mit eingebauter Erkennung abspielen.

Aufruf:
    python tools/recognition_replay.py --data DIR [--params PARAMS.json] --out OUT.json R01 R04 ...

Die Messwerte erreichen die Erkennung der Laufzeit (``runtime.detector``) wie
im Betrieb; Gangmodell, Steuerung und Nachlauf bleiben unverändert.  Die
Schützrückmeldung folgt der Heizanforderung.  Ohne ``--presence`` gilt das
Proxyverfahren (Erkennung aus Temperatur und Feuchte); mit ``--presence``
erreichen aufgezeichnete Präsenzmeldungen (Rolle ``presence``) die Laufzeit
als Präsenzsensor, sofern die Aufzeichnung ihn verwendet.  Ergebnis je
Aufzeichnung: erkannte Ereignisse, gebuchte und abgelehnte Ereignisse,
Gänge.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from custom_components.ha_sauna.bindings import Bindings  # noqa: E402
from custom_components.ha_sauna.core.models import Measurement, Position, Quantity  # noqa: E402
from custom_components.ha_sauna.core.parameters import Parameters  # noqa: E402
from custom_components.ha_sauna.core.presence import binary_presence  # noqa: E402
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


async def replay(case_dir: Path, parameters: dict | None, *, presence: bool = False):
    case = json.loads((case_dir / "case.json").read_text())
    rows = [json.loads(line) for line in gzip.open(case_dir / "samples.jsonl.gz")]
    rows.sort(key=lambda r: r["t"])
    direct = (presence and (case.get("configuration") or {}).get("presence_source") == "ha_presence"
              and any(r["role"] == "presence" for r in rows))
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
            demand = bool(c.last_decision and c.last_decision.heat)
            c.report_contactor(demand, now[0])
            c.report_heating(demand, now[0])
            sessions[c.session.session_id] = c.session
        second += 1
    recognizers = [rec for _, rec in finished] + ([runtime.detector] if runtime.detector else [])
    gangs, retracted, processed = [], [], []
    for session in sessions.values():
        timeline = session.timeline
        for g in (*timeline.completed, *([timeline.active] if timeline.active else [])):
            gangs.append({"start": iso(g.started_at), "detected": iso(g.detected_at), "end": iso(g.ended_at),
                          "end_reason": g.end_reason, "confirmation": g.confirmation.value,
                          "infusions": len(g.infusion_events)})
        retracted += [{"start": iso(g.started_at), "detected": iso(g.detected_at)} for g in timeline.retracted]
        processed += [(e.kind.value, iso(e.effective_at), iso(e.booking_at)) for e in timeline.processed
                      if e.event_id.startswith("recognition:")]
    detections = [(d.kind.value, (d.effective_at - ORIGIN).total_seconds(), (d.detected_at - ORIGIN).total_seconds())
                  for rec in recognizers for d in rec.detections]
    return {"recording": case_dir.name, "sessions": len(sessions), "gangs": gangs, "retracted": retracted,
            "detections": detections, "processed": processed, "rejected": rejected}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--params")
    ap.add_argument("--out", required=True)
    ap.add_argument("--presence", action="store_true",
                    help="aufgezeichnete Präsenzmeldungen verwenden, wenn die Aufzeichnung den Sensor nutzt")
    ap.add_argument("recordings", nargs="+")
    args = ap.parse_args()
    parameters = json.loads(Path(args.params).read_text()) if args.params else None
    out = {}
    for name in args.recordings:
        out[name] = asyncio.run(replay(Path(args.data) / name, parameters, presence=args.presence))
        g = out[name]["gangs"]
        print(name, "Gänge", len([x for x in g if x["confirmation"] == "confirmed"]),
              "verworfen", len(out[name]["retracted"]), "abgelehnt", len(out[name]["rejected"]), flush=True)
    Path(args.out).write_text(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
