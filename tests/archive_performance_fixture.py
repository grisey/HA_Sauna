"""Shared synthetic archive shape and frozen pre-optimization SQL for Linux tests."""

from contextlib import closing
from datetime import timedelta
import json
import sqlite3
import math

from custom_components.ha_sauna.core.parameters import Parameters
from test_settings import fixture

KEYS = ("upper_temperature", "upper_humidity", "lower_temperature", "lower_humidity")
INPUTS = fixture("archive")
PARAMETERS = Parameters({}).as_dict()
CONTEXT_SECONDS = PARAMETERS["history_context_seconds"]
ORIGINAL_RECORD_COUNT = (math.floor((INPUTS["duration_seconds"] + 2 * CONTEXT_SECONDS) / INPUTS["sample_interval_seconds"]) + 1) * len(KEYS)
BACKGROUND_RECORD_COUNT = INPUTS["background_records"]


def seed_volume(archive, stored, start, *, identity="volume"):
    """Bulk seed a test-only database, retaining a caller's valid session model."""
    end = start + timedelta(seconds=INPUTS["duration_seconds"])
    stored = json.loads(json.dumps(stored))
    stored["timeline"]["session_id"] = identity
    stored["timeline"]["session_started_at"] = start.isoformat()
    stored["ended_at"] = end.isoformat()
    roles = stored["configuration"]["bindings"]
    sources = tuple((roles[key], *key.split("_", 1)) for key in KEYS)
    background = json.dumps({
        "source": "sensor.synthetic_background", "position": "upper",
        "quantity": "temperature", "value": INPUTS["background_temperature"], "raw_value": str(INPUTS["background_temperature"]),
        "attributes": {"synthetic_padding": "x" * INPUTS["background_padding_chars"]},
    })
    background_at = (start - timedelta(days=INPUTS["background_age_days"])).isoformat()

    def rows():
        for _ in range(BACKGROUND_RECORD_COUNT):
            yield (archive.entry_id, "unrelated", "measurement", background_at, background)
        for sample in range(ORIGINAL_RECORD_COUNT // len(KEYS)):
            second = -CONTEXT_SECONDS + sample * INPUTS["sample_interval_seconds"]
            at = (start + timedelta(seconds=second)).isoformat()
            for source, position, quantity in sources:
                value = None if second % INPUTS["missing_every_seconds"] == 0 else INPUTS["value_base"] + second % INPUTS["value_period"] / INPUTS["value_divisor"]
                payload = json.dumps({
                    "source": source, "position": position, "quantity": quantity,
                    "received_at": at, "value": value,
                    "raw_value": None if value is None else str(value),
                })
                yield (archive.entry_id, identity if 0 <= second <= INPUTS["duration_seconds"] else None,
                       "measurement", at, payload)

    with closing(sqlite3.connect(archive.path)) as db, db:
        db.execute("INSERT INTO sessions(session_id,entry_id,started_at,updated_at,ended_at,payload) VALUES(?,?,?,?,?,?)", (
            identity, archive.entry_id, start.isoformat(), end.isoformat(), end.isoformat(),
            json.dumps(stored),
        ))
        db.executemany("INSERT INTO records(entry_id,session_id,kind,received_at,payload) "
                       "VALUES(?,?,?,?,?)", rows())
    return sources


def old_page_query(sql, parameters, sources, start):
    """Swap only the ID-union page read for the original SQL on identical input."""
    if not sql.startswith("SELECT * FROM records WHERE id IN ("):
        return sql, parameters
    kind_count = sql.split(" UNION ")[0].count("?") - 3
    kinds = parameters[3:3 + kind_count]
    kind_filter = " AND kind IN (" + ",".join("?" for _ in kinds) + ")" if kinds else ""
    end = start + timedelta(seconds=INPUTS["duration_seconds"])
    return (
        "SELECT * FROM records WHERE entry_id=? AND (session_id=? OR "
        "(kind='measurement' AND ("
        + " OR ".join(
            "(json_extract(payload,'$.source')=? AND json_extract(payload,'$.position')=? "
            "AND json_extract(payload,'$.quantity')=?)" for _ in sources
        ) + ") AND julianday(received_at) BETWEEN julianday(?) AND julianday(?) "
        "AND (julianday(received_at)<=julianday(?) OR julianday(received_at)>=julianday(?)))) "
        "AND id>?" + kind_filter + " ORDER BY id LIMIT ?",
        (parameters[0], parameters[1], *(value for source in sources for value in source),
         (start - timedelta(seconds=CONTEXT_SECONDS)).isoformat(),
         (end + timedelta(seconds=CONTEXT_SECONDS)).isoformat(), start.isoformat(), end.isoformat(),
         parameters[2], *kinds, parameters[-1]),
    )


def seed_legacy_evidence(archive, stored, start, *, identity="volume"):
    """Add dense old-format evidence without changing any measurement record.

    Four sources report every two seconds, heater feedback every minute, and
    a session revision every thirty seconds. These are synthetic load choices,
    not configuration defaults or a claim about a particular installation.
    """
    end = start + timedelta(seconds=INPUTS["duration_seconds"])
    legacy = json.loads(json.dumps(stored))
    legacy.pop("base_phases", None)
    legacy.pop("contactor_history", None)
    legacy["timeline"]["session_id"] = identity
    gangs = [
        {"gang_id": f"synthetic-{number}",
         "started_at": (start + timedelta(seconds=INPUTS["gang_start_seconds"] + number * INPUTS["gang_spacing_seconds"])).isoformat(),
         "ended_at": (start + timedelta(seconds=INPUTS["gang_end_seconds"] + number * INPUTS["gang_spacing_seconds"])).isoformat()}
        for number in range(INPUTS["gangs"])
    ]
    legacy["timeline"]["completed"] = gangs
    legacy["ready_at"] = start.isoformat()
    legacy["operation_enabled"] = False
    legacy["operation_off_at"] = end.isoformat()
    counts = {"phase": 0, "source_state": 0, "session": 0, "heater_source_state": 0}
    phase_marks = {start.isoformat(): "bereit", end.isoformat(): "aus"}
    for gang in gangs:
        phase_marks[gang["started_at"]] = "saunagang"
        phase_marks[gang["ended_at"]] = "bereit"

    def row(kind, at, payload):
        counts[kind] += 1
        return (archive.entry_id, identity, kind, at, json.dumps(payload))

    def rows():
        for second in range(0, INPUTS["duration_seconds"] + 1, INPUTS["sample_interval_seconds"]):
            at = (start + timedelta(seconds=second)).isoformat()
            if at in phase_marks:
                yield row("phase", at, {"phase": phase_marks[at]})
            # Device.ingest archives measurement roles as measurement, not
            # source_state. Use actual non-measurement roles for this load.
            for role in ("heater_power", "upper_status", "lower_status", "light"):
                yield row("source_state", at, {
                    "role": role, "source": f"sensor.synthetic_{role}",
                    "state": str(INPUTS["power_w"]) if role == "heater_power" else "on",
                    "attributes": {"synthetic_report": second,
                                   "synthetic_padding": "x" * INPUTS["source_padding_chars"]},
                })
            if second % INPUTS["heater_feedback_seconds"] == 0:
                counts["heater_source_state"] += 1
                yield row("source_state", at, {
                    "role": "heater", "state": "on" if second % INPUTS["heater_cycle_seconds"] == 0 else "off",
                })
            if second % INPUTS["session_revision_seconds"] == 0:
                snapshot = {**legacy, "timeline": {**legacy["timeline"]}}
                snapshot["timeline"]["completed"] = [g for g in gangs if g["ended_at"] <= at]
                snapshot["timeline"]["active"] = next(
                    (g for g in gangs if g["started_at"] <= at < g["ended_at"]), None,
                )
                snapshot["ended_at"] = end.isoformat() if second == INPUTS["duration_seconds"] else None
                snapshot["operation_enabled"] = second < INPUTS["duration_seconds"]
                snapshot["operation_off_at"] = end.isoformat() if second == INPUTS["duration_seconds"] else None
                yield row("session", at, snapshot)

    with closing(sqlite3.connect(archive.path)) as db, db:
        db.execute("UPDATE sessions SET payload=? WHERE entry_id=? AND session_id=?",
                   (json.dumps(legacy), archive.entry_id, identity))
        db.executemany("INSERT INTO records(entry_id,session_id,kind,received_at,payload) "
                       "VALUES(?,?,?,?,?)", rows())
    archive._invalidate_projection(identity)
    return legacy, counts
