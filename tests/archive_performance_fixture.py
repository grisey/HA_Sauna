"""Shared synthetic archive shape and frozen pre-optimization SQL for Linux tests."""

from contextlib import closing
from datetime import timedelta
import json
import sqlite3

KEYS = ("upper_temperature", "upper_humidity", "lower_temperature", "lower_humidity")
ORIGINAL_RECORD_COUNT = 46_804
BACKGROUND_RECORD_COUNT = 250_000


def seed_volume(archive, stored, start, *, identity="volume"):
    """Bulk seed a test-only database, retaining a caller's valid session model."""
    end = start + timedelta(hours=6)
    stored = json.loads(json.dumps(stored))
    stored["timeline"]["session_id"] = identity
    stored["timeline"]["session_started_at"] = start.isoformat()
    stored["ended_at"] = end.isoformat()
    roles = stored["configuration"]["bindings"]
    sources = tuple((roles[key], *key.split("_", 1)) for key in KEYS)
    background = json.dumps({
        "source": "sensor.synthetic_background", "position": "upper",
        "quantity": "temperature", "value": 19.125, "raw_value": "19.125",
        "attributes": {"synthetic_padding": "x" * 640},
    })
    background_at = (start - timedelta(days=30)).isoformat()

    def rows():
        for _ in range(BACKGROUND_RECORD_COUNT):
            yield (archive.entry_id, "unrelated", "measurement", background_at, background)
        for second in range(-900, 6 * 3600 + 901, 2):
            at = (start + timedelta(seconds=second)).isoformat()
            for source, position, quantity in sources:
                value = None if second % 997 == 0 else 40.125 + second % 101 / 8
                payload = json.dumps({
                    "source": source, "position": position, "quantity": quantity,
                    "received_at": at, "value": value,
                    "raw_value": None if value is None else str(value),
                })
                yield (archive.entry_id, identity if 0 <= second <= 21600 else None,
                       "measurement", at, payload)

    with closing(sqlite3.connect(archive.path)) as db, db:
        db.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?)", (
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
    end = start + timedelta(hours=6)
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
         (start - timedelta(minutes=15)).isoformat(),
         (end + timedelta(minutes=15)).isoformat(), start.isoformat(), end.isoformat(),
         parameters[2], *kinds, parameters[-1]),
    )
