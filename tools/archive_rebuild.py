"""N1a: Ein Sitzungsarchiv mit dem aktuellen Stand neu aufbauen (außerhalb von Home Assistant).

Je Sitzung läuft die Laufzeit dieses Stands mit ihrem Archivschreiber auf den aufgezeichneten Eingaben,
wie im Replay-Werkzeug (``recognition_replay.py``): Messwerte oben und unten im Sekundentakt, Präsenz,
Schützrückmeldung aus der Aufzeichnung (Heizzeit und Energie bleiben physikalisch), Bedienung (Betrieb
an und aus, manuelle Ofen- und Lichtwahl, Ofenkühlung beenden, Soll) und Nachträge des Nutzers
(``--nachtraege``). Neu
entstehen damit Erkennung, Gänge, Phasen, Entscheidungen, Sitzungsstände sowie Zustandsverlauf und
verworfene Kandidaten (B2). Unverändert übernommen werden Messwerte, Quellzustände, Heizbeobachtungen,
Schaltbefehle, Umgebung und die Kennzeichnung Testbetrieb. Widersprüche zwischen aufgezeichneter
Schützstellung und neu gerechneter Steuerung sind gewollt (Nutzer 10.11.2026).

Das Original wird nur gelesen; das Ziel darf nicht existieren. Jede Sitzung erhält einen Vermerk
``rebuild`` mit Softwarestand und Zeitpunkt.

Aufruf: python3 tools/archive_rebuild.py QUELLE.sqlite ZIEL.sqlite [--nachtraege N.json] [--sitzung ID]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import recognition_replay as replay

from custom_components.ha_sauna.core import (
    controller as controller_module,
)
from custom_components.ha_sauna.core.models import (
    Measurement,
    Position,
    Quantity,
)
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.presence import binary_presence
from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime
from custom_components.ha_sauna.settings import async_set_parameters

# Unverändert übernommene Einträge: was geschah oder gemessen wurde.
COPIED = ("measurement", "source_snapshot", "source_state", "heating_observation", "command",
          "light_command", "manual_light", "illuminance", "environment", "notice", "session_label",
          "calibration")
LIVE_KEYS = ("target_temperature_c", "temperature_increase_c", "final_temperature_c")
CONTEXT_SECONDS = 1


def utc(value):
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return moment.astimezone(UTC)


class SessionSource:
    """Eingaben einer Sitzung aus der Archivkopie."""

    def __init__(self, db, entry_id, row):
        self.session_id, self.started_at, self.ended_at, payload, configuration_id = row
        self.payload = json.loads(payload)
        configuration = self.payload.get("configuration")
        if configuration is None and configuration_id is not None:
            stored = db.execute("SELECT payload FROM configuration_versions WHERE id=?",
                                (configuration_id,)).fetchone()
            configuration = json.loads(stored[0]) if stored else None
        self.configuration = configuration or {}
        self.records = [
            (kind, utc(received_at), json.loads(raw))
            for kind, received_at, raw in db.execute(
                "SELECT kind,received_at,payload FROM records WHERE entry_id=? AND session_id=? "
                "ORDER BY id", (entry_id, self.session_id))
        ]
        timeline = self.payload.get("timeline") or {}
        self.start = utc(timeline.get("session_started_at") or self.started_at)
        self.end = utc(self.ended_at) if self.ended_at else None

    def of(self, kind):
        return [(at, payload) for k, at, payload in self.records if k == kind]

    def operation(self):
        """Betrieb an und aus aus den Sitzungsständen: (Zeit, an)."""
        changes, last = [], None
        for at, payload in self.of("session"):
            enabled = payload.get("operation_enabled")
            if isinstance(enabled, bool) and enabled != last:
                changes.append((at, enabled))
                last = enabled
        return changes

    def contactor(self):
        """Schützrückmeldung (KZ1): die Schützgeschichte der Sitzung.

        Ältere Sitzungen (bis 22.09.2026) speichern keine Schützgeschichte. Dort gelten die
        aufgezeichneten Zustände des Ofenschalters (``source_state`` der Rolle ``heater``), wie beim
        Lesen des Archivs in ``Archive.heating_response_history``. Ohne Rückmeldung bliebe die
        Ofenkühlung bis zum Sitzungsende ohne Beginn, und Heizzeit und Energie fehlten.
        """
        history = self.payload.get("contactor_history")
        if history:
            return sorted((utc(item["at"]), item["state"]) for item in history
                          if isinstance(item.get("state"), bool))
        heater = (self.configuration.get("bindings") or {}).get("heater")
        states = []
        for at, payload in self.of("source_state"):
            if payload.get("role") != "heater" or (heater and payload.get("source") != heater):
                continue
            state = {"on": True, "off": False}.get(payload.get("state"))
            if state is not None:
                states.append((at, state))
        return sorted(states, key=lambda item: item[0])

    def presence(self):
        out = []
        for _, payload in self.of("presence"):
            if (payload.get("assertion") == "direct_presence" and payload.get("available", True)
                    and payload.get("occupancy") in ("present", "absent")):
                out.append((utc(payload["effective_at"]), payload["occupancy"] == "present"))
        return sorted(out)


def configuration_for(source):
    """Konfiguration der Sitzung; ältere Sitzungen ohne vollständige Konfiguration mit Standards."""
    try:
        configuration = Configuration.from_options(source.configuration, historical=True)
    except (TypeError, ValueError, KeyError) as error:
        print("Konfiguration mit Standards:", source.session_id[:8], error, file=sys.stderr)
        stored = source.configuration.get("parameters") or {}
        configuration = Configuration(
            replay.Bindings(replay.BINDINGS),
            Parameters({key: value for key, value in stored.items()
                        if key in Parameters({}).values}),
            presence_source=source.configuration.get("presence_source") or "proxy",
            control_input_mode="button",
            control_mode=source.configuration.get("control_mode") or "automatic",
        )
    return configuration


def first_manual_session(session_id):
    """Die erste Kennung, die ``set_heater_override`` im Handbetrieb zieht, ist die Sitzungskennung."""
    original, used = controller_module.uuid4, [False]

    def uuid4():
        if not used[0]:
            used[0] = True
            return SimpleNamespace(hex=session_id)
        return original()

    controller_module.uuid4 = uuid4
    return lambda: setattr(controller_module, "uuid4", original)


async def rebuild_session(source, target, entry_id, corrections, stamp, log):
    configuration = configuration_for(source)
    now = [source.start - timedelta(seconds=CONTEXT_SECONDS)]
    runtime = SaunaRuntime(configuration, clock=lambda: now[0])
    await runtime.start_archive(target, entry_id)
    # Jede Sitzung des Originals bleibt erhalten, auch wenn der neue Stand keinen Gang findet;
    # die Aufbewahrungsregel des Betriebs entscheidet nur über neue Sitzungen.
    runtime.archive._retained = lambda db, session_id, data: True
    c = runtime.controller
    hass = SimpleNamespace(config_entries=SimpleNamespace(
        async_update_entry=lambda entry, options=None, **_: setattr(entry, "options", options)))
    entry = SimpleNamespace(runtime_data=runtime, options=configuration.as_options())
    presence_entity = configuration.bindings.values.get("presence")
    direct = configuration.presence_source == "ha_presence" and presence_entity
    bindings = configuration.bindings.values
    roles = {f"{p.value}_{q.value}": (p, q) for p in Position for q in Quantity}
    # Eingaben in zeitlicher Folge
    inputs = []
    for kind in ("source_snapshot", "measurement"):
        # Der Gerätezustand beim Sitzungsbeginn (source_snapshot) liefert die Anfangswerte.
        for at, payload in source.of(kind):
            # Zeit des Messwerts selbst; der Gerätezustand wird erst nach dem Start gespeichert.
            inputs.append((utc(payload.get("received_at") or at), 0, "measurement", payload))
    if direct:
        for at, value in source.presence():
            inputs.append((at, 1, "presence", value))
    for at, value in source.contactor():
        inputs.append((at, 1, "contactor", value))
    manual = configuration.control_mode == "manual"
    if not manual:
        for at, enabled in source.operation():
            inputs.append((at, 2, "operation", enabled))
    for at, payload in source.of("manual_heater"):
        if "value" in payload and not payload.get("ends_previous_only"):
            inputs.append((at, 2, "heater", payload["value"]))
    for at, payload in source.of("manual_phase_end"):
        # „Ofenkühlung beenden“ von Hand (KZ1); gilt der zu dieser Zeit laufenden Kühlung.
        if payload.get("purpose") == "after_run":
            inputs.append((at, 2, "finish_cooling", payload))
    for at, payload in source.of("parameter_change"):
        changes = {key: change.get("after") for key, change in (payload.get("changes") or {}).items()
                   if key in LIVE_KEYS}
        if changes:
            inputs.append((at, 2, "parameters", changes))
    for item in corrections.get(source.session_id, ()):
        inputs.append((utc(item["at"]), 3, item["name"], item))
    copied = [(at, 0, kind, payload) for kind, at, payload in source.records if kind in COPIED]
    inputs.sort(key=lambda item: (item[0], item[1]))
    copied.sort(key=lambda item: item[0])
    end = source.end or (inputs[-1][0] if inputs else source.start)
    contactor, contactor_at, upper_t = [None], [None], [None]
    latest, fed_to = {}, [None]
    index = copy_index = 0
    restore = None
    first = min((item[0] for item in inputs), default=source.start)
    second = max(first, source.start - timedelta(seconds=60))
    while second <= end:
        now[0] = second
        while copy_index < len(copied) and copied[copy_index][0] <= second:
            at, _, kind, payload = copied[copy_index]
            copy_index += 1
            runtime.archive.append(kind, at, payload, source.session_id)
        reports, actions = [], []
        while index < len(inputs) and inputs[index][0] <= second:
            at, _, kind, value = inputs[index]
            index += 1
            if kind not in ("measurement", "presence", "contactor"):
                actions.append((at, kind, value))
                continue
            if kind == "measurement":
                role = roles.get(f"{value['position']}_{value['quantity']}")
                number = value.get("value")
                if role is None or isinstance(number, bool) or not isinstance(number, (int, float)):
                    continue
                m = Measurement(role[0], role[1], float(number), str(value.get("raw_value", number)),
                                value.get("source") or bindings.get(f"{role[0].value}_{role[1].value}", "sensor"),
                                at)
                latest[role] = m
                if role == (Position.UPPER, Quantity.TEMPERATURE):
                    upper_t[0] = float(number)
                if runtime.detector is not None and fed_to[0] is runtime.detector:
                    runtime.detector.accept(m)
            elif kind == "presence":
                reports.append(binary_presence(presence_entity, "on" if value else "off", at, at))
            elif kind == "contactor":
                contactor[0], contactor_at[0] = value, at
        if upper_t[0] is not None:
            c.set_temperature(upper_t[0], second, valid_until=second + timedelta(seconds=30))
        # Reihenfolge wie im Replay: Betrieb, dann Präsenz, dann übrige Bedienung
        actions.sort(key=lambda item: item[1] not in ("operation", "heater"))
        presence_done = False
        for at, kind, value in [*actions, (None, "presence", None)]:
            if kind == "presence":
                continue
            if not presence_done and kind not in ("operation", "heater"):
                presence_done = True
                for report in reports:
                    await runtime.accept_presence(report)
            if kind == "operation":
                enabled = bool(c.session and c.session.operation_enabled)
                try:
                    if value and not enabled:
                        c.set_operation(True, second, session_id=source.session_id)
                    elif not value and enabled:
                        c.set_operation(False, second)
                except ValueError as error:
                    log.append((source.session_id, second.isoformat(), "operation", str(error)))
            elif kind == "heater":
                try:
                    if manual and value is True and c.session is None:
                        restore = first_manual_session(source.session_id)
                    await runtime.set_heater_override(value)
                except ValueError as error:
                    log.append((source.session_id, second.isoformat(), "heater", str(error)))
                finally:
                    if restore:
                        restore()
                        restore = None
            elif kind == "parameters":
                try:
                    await async_set_parameters(hass, entry, value, partial=True)
                except (ValueError, TypeError, KeyError) as error:
                    log.append((source.session_id, second.isoformat(), "parameters", str(error)))
            elif kind == "finish_cooling":
                phase = c.session.after_run if c.session else None
                if phase is None:
                    log.append((source.session_id, second.isoformat(), "finish_cooling",
                                "keine laufende Ofenkühlung"))
                else:
                    try:
                        await runtime.finish_phase("after_run", phase.phase_id)
                    except ValueError as error:
                        log.append((source.session_id, second.isoformat(), "finish_cooling", str(error)))
            elif kind == "end_gang":
                active = c.session.timeline.active if c.session else None
                if active is None:
                    log.append((source.session_id, second.isoformat(), "end_gang", "kein laufender Gang"))
                else:
                    await runtime.end_gang(active.gang_id)
        if not presence_done:
            for report in reports:
                await runtime.accept_presence(report)
        await runtime.tick()
        if runtime.detector is not None and fed_to[0] is not runtime.detector:
            # wie im Replay: eine neue Erkennung beginnt mit den aktuellen Werten
            fed_to[0] = runtime.detector
            for m in latest.values():
                runtime.detector.accept(m)
        if c.session is not None and contactor[0] is not None:
            replay.report_feedback(c, contactor[0], contactor_at[0], second)
        second += timedelta(seconds=1)
    if c.session is not None and c.session.session_id == source.session_id:
        c.finish_session(end, light_after_run=False)
    runtime.persist_completed_sessions()
    runtime.archive.append("rebuild", end, stamp, source.session_id)
    await runtime.close()
    sessions = [s for s in c.completed_sessions if s.session_id == source.session_id]
    return sessions[-1] if sessions else None


async def rebuild(source_path, target_path, *, corrections=None, only=None, stamp=None):
    target_path = Path(target_path)
    if target_path.exists():
        raise FileExistsError(f"Ziel existiert bereits: {target_path}")
    with closing(sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)) as db:
        entry_id = db.execute("SELECT entry_id FROM sessions LIMIT 1").fetchone()[0]
        rows = db.execute(
            "SELECT session_id,started_at,ended_at,payload,configuration_id FROM sessions "
            "WHERE entry_id=? ORDER BY started_at", (entry_id,)).fetchall()
        revision = db.execute("SELECT value FROM metadata WHERE key='revision'").fetchone()
    log, result = [], []
    for row in rows:
        if only is not None and not row[0].startswith(only):
            continue
        # Je Sitzung frisch gelesen: nur eine Sitzung liegt im Speicher.
        with closing(sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)) as db:
            source = SessionSource(db, entry_id, row)
        session = await rebuild_session(source, target_path, entry_id, corrections or {},
                                        stamp or {}, log)
        result.append((source.session_id, session))
    if target_path.exists():
        # Revision über der Quelle, damit geöffnete Ansichten nach dem Austausch neu laden.
        with closing(sqlite3.connect(target_path)) as db, db:
            own = db.execute("SELECT value FROM metadata WHERE key='revision'").fetchone()
            value = max(int(revision[0]) if revision else 0, int(own[0]) if own else 0) + 1
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('revision', ?)", (str(value),))
    return result, log


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source")
    parser.add_argument("target")
    parser.add_argument("--nachtraege")
    parser.add_argument("--sitzung")
    parser.add_argument("--stand", default="0.9.0b6")
    args = parser.parse_args()
    corrections = json.loads(Path(args.nachtraege).read_text()) if args.nachtraege else {}
    stamp = {"software": args.stand, "at": datetime.now(UTC).isoformat(),
             "source": str(Path(args.source).name)}
    result, log = asyncio.run(rebuild(args.source, args.target, corrections=corrections,
                                      only=args.sitzung, stamp=stamp))
    for session_id, session in result:
        gangs = [g for g in (session.timeline.completed if session else ())]
        print(session_id[:8], "Gänge", len(gangs), [
            (g.started_at.isoformat()[11:19], g.ended_at.isoformat()[11:19] if g.ended_at else None,
             g.end_reason) for g in gangs])
    for item in log:
        print("Hinweis", *item)


if __name__ == "__main__":
    main()
