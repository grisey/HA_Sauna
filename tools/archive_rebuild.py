"""N1a: Ein Sitzungsarchiv mit dem aktuellen Stand neu aufbauen (außerhalb von Home Assistant).

Je Sitzung läuft die Laufzeit dieses Stands mit ihrem Archivschreiber auf den aufgezeichneten Eingaben,
wie im Replay-Werkzeug (``recognition_replay.py``): Messwerte oben und unten im Sekundentakt, Präsenz,
aufgezeichneter Heizzustand (Heizzeit, Energie und Erkennung bleiben physikalisch), Bedienung (Betrieb
an und aus, manuelle Ofen- und Lichtwahl, Ofenkühlung beenden, Soll und Programmart) und Nachträge des
Nutzers (``--nachtraege``). Die Schützrückmeldung der Steuerung folgt ihrem eigenen Schaltbefehl
(Nutzer 11.11.2026, KZ3). Neu
entstehen damit Erkennung, Gänge, Phasen, Entscheidungen, Sitzungsstände sowie Zustandsverlauf und
verworfene Kandidaten (B2). Unverändert übernommen werden Messwerte, Quellzustände, Heizbeobachtungen,
Schaltbefehle, Umgebung und die Kennzeichnung Testbetrieb. Dass der aufgezeichnete Heizzustand nicht
zur neu gerechneten Steuerung passt, ist gewollt (Nutzer 10.11.2026).

Das Original wird nur gelesen; das Ziel darf nicht existieren. Jede Sitzung erhält einen Vermerk
``rebuild`` mit Softwarestand und Zeitpunkt.

Sitzungen, die ein Neustart der Integration getrennt hat, werden zusammengeführt (KZ2,
``restart_groups``).

Aufruf: python3 tools/archive_rebuild.py QUELLE.sqlite ZIEL.sqlite [--nachtraege N.json] [--sitzung ID]
        [--mindestrevision N]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
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
from custom_components.ha_sauna.core.input_handoff import (
    ContactorFeedbackInput,
    HeatingFeedbackInput,
    InputTiming,
    PowerFeedbackInput,
)
from custom_components.ha_sauna.core.models import (
    Measurement,
    Position,
    Quantity,
)
from custom_components.ha_sauna.core.parameters import LIVE_TEMPERATURE_KEYS, Parameters
from custom_components.ha_sauna.core.presence import binary_presence
from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime
from custom_components.ha_sauna.settings import async_set_parameters

# Unverändert übernommene Einträge: was geschah oder gemessen wurde.
COPIED = ("measurement", "source_snapshot", "source_state", "heating_observation", "command",
          "light_command", "manual_light", "illuminance", "environment", "notice", "session_label",
          "calibration")
# Unterbrechungen, nach denen erneutes EIN innerhalb der Wiederaufnahmefrist dieselbe Sitzung fortsetzt (KZ2)
RESTART_REASONS = ("integration_unloaded",)
CONTEXT_SECONDS = 1


def utc(value):
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return moment.astimezone(UTC)


def stored_configuration(db, payload, configuration_id):
    """Gespeicherte Konfiguration einer Sitzung, im Sitzungsstand oder als Version."""
    configuration = payload.get("configuration")
    if configuration is None and configuration_id is not None:
        stored = db.execute("SELECT payload FROM configuration_versions WHERE id=?",
                            (configuration_id,)).fetchone()
        configuration = json.loads(stored[0]) if stored else None
    return configuration or {}


def resume_window(configuration):
    """Wiederaufnahmefrist der Sitzung (``session_gap_minutes``), sonst die Werkseinstellung."""
    stored = (configuration.get("parameters") or {}).get("session_gap_minutes")
    try:
        parameters = Parameters({} if stored is None else {"session_gap_minutes": stored})
    except (TypeError, ValueError):
        parameters = Parameters({})
    return timedelta(seconds=parameters.seconds("session_gap_minutes"))


def restart_end(db, entry_id, session_id):
    """Zeit der Unterbrechung durch Entladen oder Neustart, mit der die Sitzung endet, sonst None."""
    row = db.execute(
        "SELECT received_at,payload FROM records WHERE entry_id=? AND session_id=? "
        "AND kind='interruption' ORDER BY id DESC LIMIT 1", (entry_id, session_id)).fetchone()
    if row is None or json.loads(row[1]).get("reason") not in RESTART_REASONS:
        return None
    last = db.execute("SELECT MAX(received_at) FROM records WHERE entry_id=? AND session_id=?",
                      (entry_id, session_id)).fetchone()[0]
    at = utc(row[0])
    return at if utc(last) <= at else None


def restart_groups(db, entry_id, rows):
    """KZ2: Sitzungen, die ein Neustart der Integration getrennt hat, als eine Gruppe.

    Endet eine Sitzung mit einer Unterbrechung durch Entladen oder Neustart der Integration und
    beginnt die nächste innerhalb der gespeicherten Wiederaufnahmefrist nach Betrieb AUS, gehören
    beide zusammen; im Betrieb setzt erneutes EIN innerhalb dieser Frist dieselbe Sitzung fort.
    Liefert je Gruppe die Zeilen und je Übergang (Ende, Beginn, Frist).
    """
    groups, resume = [], None
    for row in rows:
        start = utc(row[1])
        if resume is not None and resume[0] <= start <= resume[0] + resume[1]:
            groups[-1][0].append(row)
            groups[-1][1].append((resume[2], start, resume[1]))
        else:
            groups.append(([row], []))
        resume = None
        interrupted = restart_end(db, entry_id, row[0])
        if interrupted is not None:
            payload = json.loads(row[3])
            off = payload.get("operation_off_at")
            off = utc(off) if off and utc(off) <= interrupted else interrupted
            end = utc(row[2]) if row[2] else interrupted
            resume = (off, resume_window(stored_configuration(db, payload, row[4])), end)
    return groups


class SessionSource:
    """Eingaben einer Sitzung aus der Archivkopie.

    Nach einem Neustart getrennte Teile (KZ2, ``following``) bilden eine Sitzung mit der Kennung
    des ersten Teils: Ihre Einträge und sitzungslose Messwerte in der Lücke gehen gemeinsam ein,
    die Lücke wie Betrieb AUS und erneutes EIN innerhalb der Wiederaufnahmefrist.
    """

    def __init__(self, db, entry_id, row, following=()):
        self.session_id, self.started_at, self.ended_at, payload, configuration_id = row
        self.payload = json.loads(payload)
        self.configuration = stored_configuration(db, self.payload, configuration_id)
        self.session_ids = [self.session_id]
        self.parts = [(self.payload, self.configuration, self._records(db, entry_id, self.session_id))]
        self.records = list(self.parts[0][2])
        previous_end = self.ended_at or self._last(self.records, self.started_at)
        self.part_ends = []
        last_end = self.ended_at
        for later_id, later_start, later_end, later_payload, later_configuration_id in following:
            self.part_ends.append(utc(previous_end))
            gap = [(kind, utc(received_at), json.loads(raw)) for kind, received_at, raw in db.execute(
                "SELECT kind,received_at,payload FROM records WHERE entry_id=? AND session_id IS NULL "
                "AND kind='measurement' AND received_at>? AND received_at<? ORDER BY id",
                (entry_id, previous_end, later_start))]
            later_records = self._records(db, entry_id, later_id)
            later_payload = json.loads(later_payload)
            self.parts.append((later_payload, stored_configuration(db, later_payload, later_configuration_id),
                               later_records))
            self.records += gap + later_records
            self.session_ids.append(later_id)
            previous_end = later_end or self._last(later_records, later_start)
            last_end = later_end
        timeline = self.payload.get("timeline") or {}
        self.start = utc(timeline.get("session_started_at") or self.started_at)
        self.end = utc(last_end) if last_end else None

    @staticmethod
    def _last(records, default):
        """Zeit des letzten Eintrags einer Sitzung ohne gespeichertes Ende (Neustart in b6)."""
        return max(at for _, at, _ in records).isoformat() if records else default

    @staticmethod
    def _records(db, entry_id, session_id):
        return [
            (kind, utc(received_at), json.loads(raw))
            for kind, received_at, raw in db.execute(
                "SELECT kind,received_at,payload FROM records WHERE entry_id=? AND session_id=? "
                "ORDER BY id", (entry_id, session_id))
        ]

    def of(self, kind):
        return [(at, payload) for k, at, payload in self.records if k == kind]

    def operation(self):
        """Betrieb an und aus aus den Sitzungsständen: (Zeit, an).

        Endet ein Teil vor einem Neustart ohne Betrieb AUS, gilt sein Ende als Betrieb AUS.
        """
        changes, last = [], None
        for index, (_payload, _configuration, records) in enumerate(self.parts):
            for _kind, at, payload in (item for item in records if item[0] == "session"):
                enabled = payload.get("operation_enabled")
                if isinstance(enabled, bool) and enabled != last:
                    changes.append((at, enabled))
                    last = enabled
            if index < len(self.part_ends) and last:
                changes.append((self.part_ends[index], False))
                last = False
        return changes

    def contactor(self):
        """Schützrückmeldung (KZ1): die Schützgeschichte der Sitzung, je Teil.

        Ältere Sitzungen (bis 22.09.2026) speichern keine Schützgeschichte. Dort gelten die
        aufgezeichneten Zustände des Ofenschalters (``source_state`` der Rolle ``heater``), wie beim
        Lesen des Archivs in ``Archive.heating_response_history``. Ohne Rückmeldung bliebe die
        Ofenkühlung bis zum Sitzungsende ohne Beginn, und Heizzeit und Energie fehlten.
        """
        out = []
        for payload, configuration, records in self.parts:
            history = payload.get("contactor_history")
            if history:
                out += sorted((utc(item["at"]), item["state"]) for item in history
                              if isinstance(item.get("state"), bool))
                continue
            heater = (configuration.get("bindings") or {}).get("heater")
            states = []
            for kind, at, record in records:
                if kind != "source_state" or record.get("role") != "heater" or (
                        heater and record.get("source") != heater):
                    continue
                state = {"on": True, "off": False}.get(record.get("state"))
                if state is not None:
                    states.append((at, state))
            out += sorted(states, key=lambda item: item[0])
        return out

    def presence(self):
        out = []
        for _, payload in self.of("presence"):
            if (payload.get("assertion") == "direct_presence" and payload.get("available", True)
                    and payload.get("occupancy") in ("present", "absent")):
                out.append((utc(payload["effective_at"]), payload["occupancy"] == "present"))
        return sorted(out)


def report_feedback(c, command, command_at, recorded, recorded_at, at):
    """Rückmeldungen eines Takts über die Eingänge von ``HADevice.report_received_feedback`` (KZ3).

    Die Schützrückmeldung folgt dem eigenen Schaltbefehl wie im Automatikbetrieb; damit verlaufen
    Ofenkühlung und Schützgeschichte wie mit diesem Stand. Der Heizzustand folgt der Aufzeichnung:
    Heizzeit und Energie bleiben physikalisch, und die Erkennung erhält über ``controller.feedback``
    denselben Ofenzustand wie zuvor. Ohne aufgezeichneten Zustand geht nur der Befehl ein.
    """
    c.consume_contactor_feedback_input(ContactorFeedbackInput(
        contactor=command, timing=InputTiming(effective_at=command_at, received_at=command_at,
                                              first_usable_at=None, evaluated_at=at)))
    if recorded is None:
        return
    timing = InputTiming(effective_at=recorded_at, received_at=recorded_at, first_usable_at=None, evaluated_at=at)
    c.report_fallback_heating(recorded, at)
    c.consume_power_feedback_input(PowerFeedbackInput(power_w=None, valid_until=None, timing=InputTiming(
        effective_at=None, received_at=None, first_usable_at=None, evaluated_at=at)))
    c.consume_heating_feedback_input(HeatingFeedbackInput(heating=recorded, timing=timing))


def parameter_changes(source):
    """Solländerungen und Wechsel der Programmart (KZ3): (Zeit, Werte, Programmart).

    Werte sind die live änderbaren Temperaturgrößen (``LIVE_TEMPERATURE_KEYS``). Ein Eintrag mit
    ``mode_before``/``mode_after`` stammt aus dem vollständigen Änderungsweg; seine Programmart geht
    mit ein, auch wenn sie gleich bleibt.
    """
    out = []
    for at, payload in source.of("parameter_change"):
        values = {key: change.get("after") for key, change in (payload.get("changes") or {}).items()
                  if key in LIVE_TEMPERATURE_KEYS}
        mode = payload.get("mode_after") if "mode_before" in payload else None
        if values or mode is not None:
            out.append((at, values, mode))
    return out


def configuration_for(source):
    """Konfiguration der Sitzung; ältere Sitzungen ohne vollständige Konfiguration mit Standards.

    Gespeichert ist der Stand am Sitzungsende. Wechselte die Programmart in der Sitzung, beginnt
    die Sitzung mit der Programmart vor dem ersten Wechsel (KZ3).
    """
    configuration = stored_configuration_for(source)
    first = next((payload for _, payload in source.of("parameter_change") if "mode_before" in payload), None)
    if first is not None and first["mode_before"] != configuration.program_mode:
        try:
            configuration = replace(configuration, program_mode=first["mode_before"])
        except (TypeError, ValueError) as error:
            print("Programmart beim Beginn:", source.session_id[:8], error, file=sys.stderr)
    return configuration


def stored_configuration_for(source):
    """Gespeicherte Konfiguration der Sitzung; ohne vollständige Konfiguration mit Standards."""
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
    for at, values, mode in parameter_changes(source):
        inputs.append((at, 2, "parameters", (values, mode)))
    for item in (item for session_id in source.session_ids for item in corrections.get(session_id, ())):
        inputs.append((utc(item["at"]), 3, item["name"], item))
    copied = [(at, 0, kind, payload) for kind, at, payload in source.records if kind in COPIED]
    inputs.sort(key=lambda item: (item[0], item[1]))
    copied.sort(key=lambda item: item[0])
    end = source.end or (inputs[-1][0] if inputs else source.start)
    contactor, contactor_at, upper_t = [None], [None], [None]
    commanded = [None, None]
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
                    await async_set_parameters(hass, entry, value[0], partial=True, program_mode=value[1])
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
        if c.session is not None:
            # wie ``recognition_replay.py --heater split``: die Rückmeldung folgt dem Befehl des Takts
            command = bool(c.last_decision and c.last_decision.heat)
            if command is not commanded[0] or commanded[1] is None:
                commanded[:] = [command, second]
            report_feedback(c, commanded[0], commanded[1], contactor[0], contactor_at[0], second)
        second += timedelta(seconds=1)
    # Übernommene Einträge nach dem letzten Sekundenschritt gehören ebenfalls zur Sitzung (KZ2).
    for at, _, kind, payload in copied[copy_index:]:
        runtime.archive.append(kind, at, payload, source.session_id)
    if c.session is not None and c.session.session_id == source.session_id:
        c.finish_session(end, light_after_run=False)
    runtime.persist_completed_sessions()
    if len(source.session_ids) > 1:
        stamp = {**stamp, "merged_session_ids": source.session_ids}
    runtime.archive.append("rebuild", end, stamp, source.session_id)
    await runtime.close()
    sessions = [s for s in c.completed_sessions if s.session_id == source.session_id]
    return sessions[-1] if sessions else None


async def rebuild(source_path, target_path, *, corrections=None, only=None, stamp=None,
                  minimum_revision=0):
    target_path = Path(target_path)
    if target_path.exists():
        raise FileExistsError(f"Ziel existiert bereits: {target_path}")
    with closing(sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)) as db:
        entry_id = db.execute("SELECT entry_id FROM sessions LIMIT 1").fetchone()[0]
        rows = db.execute(
            "SELECT session_id,started_at,ended_at,payload,configuration_id FROM sessions "
            "WHERE entry_id=? ORDER BY started_at", (entry_id,)).fetchall()
        revision = db.execute("SELECT value FROM metadata WHERE key='revision'").fetchone()
        groups = restart_groups(db, entry_id, rows)
    log, result = [], []
    for group, transitions in groups:
        if only is not None and not any(row[0].startswith(only) for row in group):
            continue
        for row, (ended, resumed, window) in zip(group[1:], transitions):
            gap = (resumed - ended).total_seconds()
            log.append((group[0][0], ended.isoformat(), "merge",
                        (f"{row[0]} nach Neustart fortgesetzt; Lücke {gap:.0f} s, "
                         f"Wiederaufnahmefrist {window.total_seconds() / 60:g} min")))
        # Je Sitzung frisch gelesen: nur eine Sitzung liegt im Speicher.
        with closing(sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)) as db:
            source = SessionSource(db, entry_id, group[0], group[1:])
        session = await rebuild_session(source, target_path, entry_id, corrections or {},
                                        stamp or {}, log)
        result.append((source.session_id, session))
    if target_path.exists():
        # Revision über der Quelle und mindestens ``minimum_revision``, damit geöffnete Ansichten
        # nach dem Austausch neu laden, auch wenn das ersetzte Archiv seit der Kopie weitergezählt hat.
        with closing(sqlite3.connect(target_path)) as db, db:
            own = db.execute("SELECT value FROM metadata WHERE key='revision'").fetchone()
            value = max(int(revision[0]) if revision else 0, int(own[0]) if own else 0) + 1
            value = max(value, minimum_revision)
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('revision', ?)", (str(value),))
    return result, log


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source")
    parser.add_argument("target")
    parser.add_argument("--nachtraege")
    parser.add_argument("--sitzung")
    parser.add_argument("--stand", default="0.9.0b6")
    parser.add_argument("--mindestrevision", type=int, default=0)
    args = parser.parse_args()
    corrections = json.loads(Path(args.nachtraege).read_text()) if args.nachtraege else {}
    stamp = {"software": args.stand, "at": datetime.now(UTC).isoformat(),
             "source": str(Path(args.source).name)}
    result, log = asyncio.run(rebuild(args.source, args.target, corrections=corrections,
                                      only=args.sitzung, stamp=stamp,
                                      minimum_revision=args.mindestrevision))
    for session_id, session in result:
        gangs = [g for g in (session.timeline.completed if session else ())]
        print(session_id[:8], "Gänge", len(gangs), [
            (g.started_at.isoformat()[11:19], g.ended_at.isoformat()[11:19] if g.ended_at else None,
             g.end_reason) for g in gangs])
    for item in log:
        print("Hinweis", *item)


if __name__ == "__main__":
    main()
