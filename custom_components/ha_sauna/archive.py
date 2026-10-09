"""Originalauflösung und nachvollziehbare Zustandsrevisionen in privatem SQLite."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import sqlite3
import tempfile
import threading
import zipfile
from collections import OrderedDict, deque
from collections.abc import Mapping
from contextlib import closing
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from math import isfinite
from pathlib import Path

from .core.defaults import section
from .core.history import HISTORY_CONTEXT_SECONDS, measurement_window
from .core.phases import project_archive, project_session

_LOGGER = logging.getLogger(__name__)


def plain(value):
    if is_dataclass(value):
        return {
            field.name: plain(getattr(value, field.name)) for field in fields(value)
        }
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [plain(v) for v in value]
    return value


def encoded(value):
    return encoded_plain(plain(value))


def encoded_plain(value):
    """Encode an already converted value with the archive's exact JSON contract."""
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )


def session_has_gangs(session):
    """Count the same confirmed rounds as the timeline, including interruption."""
    timeline = session.get("timeline", {})
    gangs = list(timeline.get("completed", ()))
    if timeline.get("active"):
        gangs.append(timeline["active"])
    return any(gang.get("infusion_events")
               or gang.get("recognition_kind") == "presence_confirmed" for gang in gangs)


class _ExportWork:
    """Own the temporary file across both thread completion and caller cancellation."""

    def __init__(self, create):
        self.create = create
        self.lock = threading.Lock()
        self.abandoned = False
        self.path = None

    def run(self):
        path = self.create()
        with self.lock:
            if not self.abandoned:
                self.path = path
                return path
        self._remove(path)
        return None

    def discard(self):
        with self.lock:
            self.abandoned = True
            path, self.path = self.path, None
        if path is not None:
            self._remove(path)

    @staticmethod
    def _remove(path):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            _LOGGER.exception("Abgebrochenen Saunaexport konnte nicht entfernt werden")


class Archive:
    def __init__(self, path, entry_id, *, sessions_only=False):
        self.path = Path(path)
        self.entry_id = entry_id
        self.queue = asyncio.Queue()
        self.resume = asyncio.Event()
        self.resume.set()
        self.worker = None
        self.failure = None
        self.failed_records = deque()
        self.closed = False
        self._close_task = None
        self.sessions_only = sessions_only
        self._discarded_sessions = set()
        self._projection_cache = OrderedDict()
        self._projection_lock = threading.Lock()
        self._projection_generation = 0
        self._projection_cache_entries = section("runtime")["archive_projection_cache_entries"]
        self.revision = 0
        self._context_pruned_before = None

    async def start(self):
        await asyncio.to_thread(self._initialize)
        self.worker = asyncio.create_task(
            self._run(), name=f"ha_sauna_archive_{self.entry_id}"
        )

    def _initialize(self, now=None):
        now = now or datetime.now(UTC)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript("""
                PRAGMA journal_mode=DELETE;
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT OR IGNORE INTO metadata VALUES ('schema', '1');
                CREATE TABLE IF NOT EXISTS records (
                    id INTEGER PRIMARY KEY, entry_id TEXT NOT NULL, session_id TEXT,
                    kind TEXT NOT NULL, received_at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS records_session ON records(entry_id, session_id, id);
                DROP INDEX IF EXISTS records_measurement_clock;
                CREATE INDEX IF NOT EXISTS records_measurement_context ON records(entry_id, julianday(received_at))
                    WHERE kind='measurement';
                CREATE INDEX IF NOT EXISTS records_consumer_events ON records(entry_id)
                    WHERE kind='consumer_event';
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY, entry_id TEXT NOT NULL,
                    started_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    ended_at TEXT, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS consumer_receipts (
                    entry_id TEXT NOT NULL, event_id TEXT NOT NULL,
                    PRIMARY KEY(entry_id,event_id));
            """)
            if (
                db.execute("SELECT value FROM metadata WHERE key='schema'").fetchone()[
                    0
                ]
                != "1"
            ):
                raise ValueError("Nicht unterstützte Archivversion")
            row = db.execute("SELECT value FROM metadata WHERE key='revision'").fetchone()
            self.revision = int(row[0]) if row else 0
            before_cleanup = db.total_changes
            if self.sessions_only:
                for (payload,) in db.execute(
                    "SELECT payload FROM records WHERE entry_id=? AND session_id IS NULL AND kind='consumer_event'",
                    (self.entry_id,),
                ).fetchall():
                    event_id = json.loads(payload).get("event_id")
                    if event_id:
                        db.execute("INSERT OR IGNORE INTO consumer_receipts VALUES(?,?)",
                                   (self.entry_id, event_id))
                # Startup has no resumed live session. Retire old empty and
                # interrupted attempts; retain bounded measurement context.
                for row in db.execute(
                    "SELECT session_id,payload FROM sessions WHERE entry_id=?",
                    (self.entry_id,),
                ).fetchall():
                    if not session_has_gangs(json.loads(row[1])):
                        self._delete_session(db, row[0])
                db.execute(
                    "DELETE FROM records WHERE entry_id=? AND "
                    "((session_id IS NULL AND kind!='measurement') OR "
                    "session_id NOT IN (SELECT session_id FROM sessions WHERE entry_id=?))",
                    (self.entry_id, self.entry_id),
                )
                self._prune_context(db, now)
                if db.total_changes > before_cleanup:
                    self.revision = self._bump_revision(db)

    def append(self, kind, at, payload, session_id=None):
        if self.closed:
            raise RuntimeError("Archiv ist geschlossen")
        if self.sessions_only and session_id is None and kind not in {"consumer_event", "measurement"}:
            return
        self._append_plain(kind, at, plain(payload), session_id)

    def _append_plain(self, kind, at, payload, session_id=None):
        if self.closed:
            raise RuntimeError("Archiv ist geschlossen")
        if self.sessions_only and session_id is None and kind != "measurement":
            if kind != "consumer_event":
                return
            # Delivery identity only, without measurements or a session log.
            kind, payload = "consumer_receipt", {"event_id": payload["event_id"]}
        # Sofort kopieren: spätere Zustandsänderungen verändern keinen Auftrag.
        self.queue.put_nowait(
            ("record", (kind, at.isoformat(), encoded_plain(payload), session_id))
        )

    def save_session(self, session, at, configuration):
        payload = plain(session)
        payload["configuration"] = plain(configuration)
        self._append_plain("session", at, payload, session.session_id)

    async def _run(self):
        while True:
            kind, payload = await self.queue.get()
            try:
                if kind == "stop":
                    return
                future = payload[2] if kind == "erase" else payload
                if kind in {"fence", "pause", "erase"} and future.cancelled():
                    continue
                # A long-lived SQLite reader can make each attempt block for
                # seconds.  While records already form one queue burst, keep
                # their originals in order and retry only when catching up.
                if kind == "record" and self.failed_records and not self.queue.empty():
                    self.failed_records.append(payload)
                    continue
                if not await self._drain_failed_records():
                    if kind == "record":
                        self.failed_records.append(payload)
                    elif kind in {"fence", "pause", "erase"} and not future.cancelled():
                        future.set_exception(self.failure)
                    continue
                if kind in {"fence", "pause"} and payload.cancelled():
                    continue
                if kind == "record":
                    await asyncio.to_thread(self._write, payload)
                elif kind == "erase":
                    result = await asyncio.to_thread(self._erase, payload[0], payload[1])
                    if not future.cancelled():
                        future.set_result(result)
                self.failure = None
                if kind == "fence":
                    payload.set_result(None)
                elif kind == "pause":
                    payload.set_result(None)
                    await self.resume.wait()
            except Exception as error:
                if kind != "erase" or not isinstance(error, (KeyError, ValueError)):
                    self.failure = error
                if kind == "record":
                    self.failed_records.append(payload)
                elif kind in {"fence", "pause", "erase"} and not future.cancelled():
                    future.set_exception(error)
            finally:
                self.queue.task_done()

    async def _drain_failed_records(self):
        """Persist the oldest failed records once, without a retry loop.

        A record stays at the head until it was committed.  This makes a later
        session snapshot unable to overtake an earlier one, and leaves the
        current error observable until every pending original has been saved.
        """
        while self.failed_records:
            try:
                await asyncio.to_thread(self._write, self.failed_records[0])
            except Exception as error:
                self.failure = error
                return False
            self.failed_records.popleft()
        return True

    def _write(self, record):
        kind, at, payload, session_id = record
        if session_id in self._discarded_sessions:
            return
        with closing(sqlite3.connect(self.path)) as db, db:
            if kind == "consumer_receipt":
                db.execute("INSERT OR IGNORE INTO consumer_receipts VALUES(?,?)",
                           (self.entry_id, json.loads(payload)["event_id"]))
                return
            if kind == "session" and self.sessions_only:
                data = json.loads(payload)
                if data.get("ended_at") and not session_has_gangs(data):
                    self._delete_session(db, session_id)
                    self._prune_context(db, datetime.fromisoformat(at))
                    revision = self._bump_revision(db)
                    db.commit()
                    self.revision = revision
                    self._discarded_sessions.add(session_id)
                    self._invalidate_projection(session_id)
                    return
            db.execute(
                "INSERT INTO records(entry_id,session_id,kind,received_at,payload) VALUES(?,?,?,?,?)",
                (self.entry_id, session_id, kind, at, payload),
            )
            if kind == "session":
                data = json.loads(payload)
                db.execute(
                    """INSERT INTO sessions VALUES(?,?,?,?,?,?)
                    ON CONFLICT(session_id) DO UPDATE SET updated_at=excluded.updated_at,
                    ended_at=excluded.ended_at,payload=excluded.payload""",
                    (
                        session_id,
                        self.entry_id,
                        data["timeline"]["session_started_at"],
                        at,
                        data["ended_at"],
                        payload,
                    ),
                )
            if self.sessions_only and kind in {"measurement", "session"}:
                self._prune_context(db, datetime.fromisoformat(at), incremental=True)
        if kind in {"phase", "source_state", "session"}:
            self._invalidate_projection(session_id)

    def _invalidate_projection(self, session_id=None):
        with self._projection_lock:
            self._projection_generation += 1
            if session_id is None:
                self._projection_cache.clear()
            else:
                self._projection_cache.pop(session_id, None)

    def _delete_session(self, db, session_id):
        # A measurement may still provide context for another retained session.
        db.execute(
            "UPDATE records SET session_id=NULL WHERE entry_id=? AND session_id=? AND kind='measurement'",
            (self.entry_id, session_id),
        )
        db.execute("DELETE FROM records WHERE entry_id=? AND session_id=?",
                   (self.entry_id, session_id))
        db.execute("DELETE FROM sessions WHERE entry_id=? AND session_id=?",
                   (self.entry_id, session_id))

    def _prune_context(self, db, now, *, incremental=False):
        """Revisit newly expired buffer points; full scans only at lifecycle changes."""
        cutoff = now - timedelta(seconds=HISTORY_CONTEXT_SECONDS)
        previous = self._context_pruned_before if incremental else None
        if previous is not None and cutoff <= previous:
            return
        lower_bound = " AND julianday(received_at)>=julianday(?)" if previous else ""
        db.execute(
            """DELETE FROM records WHERE entry_id=? AND session_id IS NULL
            AND kind='measurement' AND julianday(received_at)<julianday(?)"""
            + lower_bound + """
            AND NOT EXISTS (
                SELECT 1 FROM sessions s WHERE s.entry_id=records.entry_id
                AND julianday(records.received_at)>=julianday(s.started_at)-?/86400.0
                AND julianday(records.received_at)<=julianday(COALESCE(s.ended_at, s.updated_at))+?/86400.0
                AND (julianday(records.received_at)<=julianday(s.started_at)
                    OR julianday(records.received_at)>=julianday(COALESCE(s.ended_at, s.updated_at)))
                AND json_extract(records.payload,'$.position') IN ('upper','lower')
                AND json_extract(records.payload,'$.quantity') IN ('temperature','humidity')
                AND json_extract(s.payload,'$.configuration.bindings.' ||
                    json_extract(records.payload,'$.position') || '_' ||
                    json_extract(records.payload,'$.quantity'))=json_extract(records.payload,'$.source')
            )""",
            (self.entry_id, cutoff.isoformat(),
             *((previous.isoformat(),) if previous else ()),
             HISTORY_CONTEXT_SECONDS, HISTORY_CONTEXT_SECONDS),
        )
        self._context_pruned_before = cutoff if incremental else None

    def _bump_revision(self, db):
        revision = self.revision + 1
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('revision', ?)",
                   (str(revision),))
        return revision

    def _erase(self, session_id, reset, now=None):
        now = now or datetime.now(UTC)
        with closing(sqlite3.connect(self.path)) as db, db:
            if reset:
                ids = [row[0] for row in db.execute(
                    "SELECT session_id FROM sessions WHERE entry_id=?", (self.entry_id,)
                )]
                db.execute("DELETE FROM records WHERE entry_id=?", (self.entry_id,))
                db.execute("DELETE FROM sessions WHERE entry_id=?", (self.entry_id,))
                db.execute("DELETE FROM consumer_receipts WHERE entry_id=?", (self.entry_id,))
                self._context_pruned_before = None
            else:
                row = db.execute(
                    "SELECT 1 FROM sessions WHERE entry_id=? AND session_id=?",
                    (self.entry_id, session_id),
                ).fetchone()
                if row is None:
                    raise KeyError(session_id)
                ids = [session_id]
                self._delete_session(db, session_id)
                self._prune_context(db, now)
            revision = self._bump_revision(db)
            db.commit()
            self.revision = revision
            self._discarded_sessions.update(ids)
        self._invalidate_projection()
        return len(ids)

    async def erase(self, session_id=None, *, reset=False):
        """Delete in writer order; the runtime guards actual activity under its lock.

        An absent stored end is not an activity signal after restart. Originals
        remain unchanged until deletion, and late writes cannot recreate them.
        """
        if self.closed:
            raise RuntimeError("Archiv ist geschlossen")
        future = asyncio.get_running_loop().create_future()
        self.queue.put_nowait(("erase", (session_id, reset, future)))
        return await asyncio.shield(future)

    async def _barrier(self, kind):
        future = asyncio.get_running_loop().create_future()
        self.queue.put_nowait((kind, future))
        await future

    async def flush(self):
        if self._close_task is not None:
            await asyncio.shield(self._close_task)
        else:
            await self._barrier("fence")

    async def pre_backup(self):
        # Alle bisherigen Aufträge sind dauerhaft geschrieben. Nur der Schreiber
        # pausiert; Regelung und Eingangserfassung dürfen weiterarbeiten.
        if self._close_task is not None:
            # Closing rejects new records, but its queued writes may still be
            # running. Finish them before the backup may copy this database.
            await asyncio.shield(self._close_task)
            return
        if not self.resume.is_set():
            raise RuntimeError("Archiv wird bereits gesichert")
        # Establish ownership before enqueueing: a release while the writer is
        # catching up must not be lost when it eventually reaches the pause.
        self.resume.clear()
        try:
            await self._barrier("pause")
        except BaseException:
            self.release_backup()
            raise

    def release_backup(self):
        """Release a backup pause without waiting for pending writes."""
        self.resume.set()

    async def close(self):
        if self._close_task is None:
            self.closed = True
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)

    async def _close(self):
        if self.worker is None:
            return
        try:
            # Only the backup owner may release its pause. Its post-hook keeps
            # this archive reachable even after the runtime begins unloading.
            await self._barrier("fence")
        finally:
            self.queue.put_nowait(("stop", None))
            await self.worker

    def read(self, session_id=None, *, after=0, limit=1000, kinds=None, now=None):
        # Capture before establishing the SQLite snapshot. An in-flight reader
        # may finish after invalidation, but must not restore its old cache data.
        with self._projection_lock:
            generation = self._projection_generation
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN")
            if session_id is None:
                rows = db.execute(
                    "SELECT session_id,started_at,updated_at,ended_at FROM sessions WHERE entry_id=? ORDER BY started_at DESC",
                    (self.entry_id,),
                )
                return [dict(row) for row in rows]
            row = db.execute(
                "SELECT payload,updated_at FROM sessions WHERE entry_id=? AND session_id=?",
                (self.entry_id, session_id),
            ).fetchone()
            if row is None:
                return None
            kind_filter = (
                " AND kind IN (" + ",".join("?" for _ in kinds) + ")"
                if kinds is not None else ""
            )
            session = json.loads(row["payload"])
            current = now or datetime.now(UTC)
            started = datetime.fromisoformat(session["timeline"]["session_started_at"])
            ended = datetime.fromisoformat(session["ended_at"]) if session.get("ended_at") else None
            window = measurement_window(started, ended, current)
            bindings = session.get("configuration", {}).get("bindings", {})
            sources = tuple(
                (bindings[key], *key.split("_", 1)) for key in (
                    "upper_temperature", "upper_humidity", "lower_temperature", "lower_humidity"
                ) if key in bindings
            )
            context_filter = ""
            context_args = ()
            if sources:
                context_filter = (
                    " OR (kind='measurement' AND ("
                    + " OR ".join(
                        "(json_extract(payload,'$.source')=? AND json_extract(payload,'$.position')=? "
                        "AND json_extract(payload,'$.quantity')=?)" for _ in sources
                    ) + ")"
                    " AND julianday(received_at) BETWEEN julianday(?) AND julianday(?)"
                    " AND (julianday(received_at)<=julianday(?) OR julianday(received_at)>=julianday(?)))"
                )
                context_args = (*(value for source in sources for value in source),
                                window["started_at"], window["ended_at"],
                                started.isoformat(), (ended or current).isoformat())
            records = db.execute(
                "SELECT * FROM records WHERE entry_id=? AND (session_id=?"
                + context_filter + ") AND id>?" + kind_filter + " ORDER BY id LIMIT ?",
                (self.entry_id, session_id, *context_args, after, *(kinds or ()), limit),
            ).fetchall()
            if session.get("base_phases"):
                projection = project_session(session, row["updated_at"])
            else:
                projection = self._legacy_projection(
                    db, session_id, session, row, generation=generation
                )
            return {
                "session": session,
                "measurement_window": window,
                "phase_projection": plain(projection),
                "records": [
                    {**dict(r), "payload": json.loads(r["payload"])} for r in records
                ],
                "next_after": records[-1]["id"] if len(records) == limit else None,
            }

    def _legacy_projection(self, db, session_id, session, row, *, generation):
        # Read cache identity and evidence in the caller's SQLite snapshot. In
        # particular, a concurrent old reader cannot publish a reusable stale
        # result after a write/delete has invalidated the in-memory entry.
        last_evidence = db.execute(
            "SELECT MAX(id) FROM records WHERE entry_id=? AND session_id=? "
            "AND kind IN ('phase','source_state','session')",
            (self.entry_id, session_id),
        ).fetchone()[0]
        revision = db.execute("SELECT value FROM metadata WHERE key='revision'").fetchone()
        identity = (row["payload"], row["updated_at"], last_evidence,
                    revision[0] if revision else None)
        with self._projection_lock:
            current = generation == self._projection_generation
            cached = self._projection_cache.get(session_id) if current else None
            if cached is not None and cached[0] == identity:
                self._projection_cache.move_to_end(session_id)
                return cached[1]
            # Legacy snapshots need the complete evidence stream once per
            # saved state, independently of pagination. Originals stay intact.
            evidence = (
                {
                    "kind": r["kind"],
                    "received_at": r["received_at"],
                    "payload": json.loads(r["payload"]),
                }
                for r in db.execute(
                    "SELECT kind,received_at,payload FROM records WHERE entry_id=? AND session_id=? AND kind IN ('phase','source_state','session') ORDER BY id",
                    (self.entry_id, session_id),
                )
            )
            projection = project_archive(session, evidence, row["updated_at"])
            if current:
                self._projection_cache[session_id] = (identity, projection)
                self._projection_cache.move_to_end(session_id)
                while len(self._projection_cache) > self._projection_cache_entries:
                    self._projection_cache.popitem(last=False)
            return projection

    def consumer_event_ids(self):
        """Stable delivered identities for reload deduplication, read only."""
        with closing(sqlite3.connect(self.path)) as db:
            return {
                event_id
                for (payload,) in db.execute(
                    "SELECT payload FROM records WHERE entry_id=? AND kind='consumer_event'",
                    (self.entry_id,),
                )
                if isinstance((event_id := json.loads(payload).get("event_id")), str)
            } | {
                row[0] for row in db.execute(
                    "SELECT event_id FROM consumer_receipts WHERE entry_id=?", (self.entry_id,)
                )
            }

    def latest_completed_warmup(self, source, maximum_gap_seconds):
        """Read upper measurements of the last archived initial warm-up only.

        This deliberately makes a few narrowly scoped queries for one completed
        session.  Callers run it in a worker thread and cache its result; it is
        not a history endpoint and never scans measurements from other sessions.
        """
        if (
            not isinstance(source, str)
            or not source
            or isinstance(maximum_gap_seconds, bool)
            or not isinstance(maximum_gap_seconds, (int, float))
            or not isfinite(maximum_gap_seconds)
            or maximum_gap_seconds <= 0
        ):
            return None
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute(
                """SELECT session_id,ended_at FROM sessions
                WHERE entry_id=? AND ended_at IS NOT NULL
                ORDER BY ended_at DESC LIMIT 1""",
                (self.entry_id,),
            ).fetchone()
            if row is None:
                return None
            session_id, session_ended = row
            phases = db.execute(
                """SELECT received_at,payload FROM records
                WHERE entry_id=? AND session_id=? AND kind='phase' ORDER BY id""",
                (self.entry_id, session_id),
            ).fetchall()
            started_at = ended_at = None
            for received_at, payload in phases:
                try:
                    at = datetime.fromisoformat(received_at)
                    phase = json.loads(payload).get("phase")
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if started_at is None:
                    if phase == "aufheizen":
                        started_at = at
                elif phase != "aufheizen":
                    ended_at = at
                    break
            if started_at is None:
                return {"session_id": session_id, "measurements": ()}
            if ended_at is None:
                try:
                    ended_at = datetime.fromisoformat(session_ended)
                except (TypeError, ValueError):
                    return {"session_id": session_id, "measurements": ()}
            if ended_at <= started_at:
                return {"session_id": session_id, "measurements": ()}

            rows = db.execute(
                """SELECT received_at,payload FROM records
                WHERE entry_id=? AND session_id=? AND kind='measurement'
                AND received_at>=? AND received_at<? ORDER BY id""",
                (
                    self.entry_id,
                    session_id,
                    started_at.isoformat(),
                    ended_at.isoformat(),
                ),
            ).fetchall()
        measurements = []
        for received_at, payload in rows:
            try:
                data = json.loads(payload)
                at = datetime.fromisoformat(received_at)
                value = data["value"]
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                return {"session_id": session_id, "measurements": ()}
            if data.get("position") != "upper" or data.get("quantity") != "temperature":
                continue
            if (
                data.get("source") != source
                or isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                or at < started_at
                or at >= ended_at
            ):
                return {"session_id": session_id, "measurements": ()}
            measurements.append((at, float(value)))
        if (
            not measurements
            or (measurements[0][0] - started_at).total_seconds() > maximum_gap_seconds
            or (ended_at - measurements[-1][0]).total_seconds() > maximum_gap_seconds
            or any(
                (right[0] - left[0]).total_seconds() > maximum_gap_seconds
                for left, right in zip(measurements, measurements[1:])
            )
        ):
            return {"session_id": session_id, "measurements": ()}
        return {"session_id": session_id, "measurements": tuple(measurements)}

    def heating_response_history(self, source, position, heater_source, maximum_gap_seconds):
        """Read complete, uninterrupted OFF/ON/OFF observations for this hardware.

        Original receipt times remain the measurement clock. A session's first
        ON snapshot is a state, never evidence of a switching edge. Callers run
        this historical read outside HA's event loop and cache the result.
        """
        if (
            not isinstance(source, str) or not source
            or not isinstance(position, str) or position not in {"upper", "lower"}
            or not isinstance(heater_source, str) or not heater_source
            or isinstance(maximum_gap_seconds, bool)
            or not isinstance(maximum_gap_seconds, (int, float))
            or not isfinite(maximum_gap_seconds) or maximum_gap_seconds <= 0
        ):
            return ()

        def timestamp(value):
            at = datetime.fromisoformat(value)
            if at.tzinfo is None:
                raise ValueError("Historical evidence requires a timezone")
            return at

        episodes = []
        with closing(sqlite3.connect(self.path)) as db:
            # Configuration, state edges and measurements share one read snapshot.
            db.execute("BEGIN")
            sessions = db.execute(
                "SELECT session_id,payload FROM sessions "
                "WHERE entry_id=? AND ended_at IS NOT NULL ORDER BY ended_at,session_id",
                (self.entry_id,),
            ).fetchall()
            for session_id, payload in sessions:
                try:
                    session = json.loads(payload)
                    bindings = session.get("configuration", {}).get("bindings", {})
                    if (
                        bindings.get(f"{position}_temperature") != source
                        or bindings.get("heater") != heater_source
                    ):
                        continue
                    session_start = timestamp(session["timeline"]["session_started_at"])
                    session_end = timestamp(session["ended_at"])
                    rows = db.execute(
                        "SELECT kind,received_at,payload FROM records "
                        "WHERE entry_id=? AND session_id=? "
                        "AND kind IN ('measurement','source_state','detection') ORDER BY id",
                        (self.entry_id, session_id),
                    ).fetchall()
                    contacts = []
                    measurements = []
                    doors = {
                        (timestamp(event["effective_at"]), event["kind"])
                        for event in session["timeline"].get("processed", ())
                        if event.get("kind") in {"door_open", "door_close"}
                    }
                    native_contacts = session.get("contactor_history")
                    if native_contacts:
                        contacts = [
                            (timestamp(mark["at"]), mark["state"])
                            for mark in native_contacts
                        ]
                    for kind, received_at, raw in rows:
                        data = json.loads(raw)
                        if kind == "measurement":
                            if data.get("position") != position or data.get("quantity") != "temperature":
                                continue
                            at = timestamp(received_at)
                            value = data.get("value")
                            valid = (
                                data.get("source") == source
                                and not isinstance(value, bool)
                                and isinstance(value, (int, float)) and isfinite(value)
                            )
                            measurements.append((at, float(value) if valid else None))
                        elif kind == "source_state" and not native_contacts:
                            if data.get("role") == "heater":
                                state = (
                                    {"on": True, "off": False}.get(data.get("state"))
                                    if data.get("source") == heater_source else None
                                )
                                contacts.append((timestamp(received_at), state))
                        elif kind == "detection":
                            event = data.get("event", {})
                            if event.get("kind") in {"door_open", "door_close"}:
                                doors.add((timestamp(event["effective_at"]), event["kind"]))
                    if any(state is not None and not isinstance(state, bool) for _, state in contacts):
                        continue
                    if any(right[0] < left[0] for left, right in zip(contacts, contacts[1:])):
                        continue
                    measurements.sort(key=lambda item: item[0])
                    doors = sorted(doors)
                    previous = None
                    started_at = None
                    for at, state in contacts:
                        if not session_start <= at <= session_end:
                            previous, started_at = None, None
                            continue
                        if state is True and previous is False:
                            started_at = at
                        elif state is not True and started_at is not None:
                            if state is False and at > started_at:
                                # An opening before ON remains relevant until its
                                # close. Either door edge during ON invalidates it.
                                before = [kind for when, kind in doors if when < started_at]
                                disturbed = (
                                    (before and before[-1] == "door_open")
                                    or any(started_at <= when <= at for when, _ in doors)
                                )
                                samples = [item for item in measurements if started_at <= item[0] <= at]
                                preceding = [item for item in measurements if item[0] < started_at]
                                if preceding and preceding[-1][1] is not None and (
                                    started_at - preceding[-1][0]
                                ).total_seconds() <= maximum_gap_seconds:
                                    samples.insert(0, preceding[-1])
                                if (
                                    not disturbed and samples
                                    and all(value is not None for _, value in samples)
                                    and (samples[0][0] - started_at).total_seconds() <= maximum_gap_seconds
                                    and (at - samples[-1][0]).total_seconds() <= maximum_gap_seconds
                                    and all(
                                        (right[0] - left[0]).total_seconds() <= maximum_gap_seconds
                                        for left, right in zip(samples, samples[1:])
                                    )
                                ):
                                    episodes.append({
                                        "session_id": session_id,
                                        "started_at": started_at,
                                        "ended_at": at,
                                        "measurements": tuple(samples),
                                    })
                            started_at = None
                        previous = state
                except (KeyError, TypeError, ValueError, AttributeError):
                    # Incomplete legacy evidence cannot teach a heating delay.
                    continue
        return tuple(episodes)

    async def export(self):
        await self.flush()
        work = _ExportWork(self._export)
        try:
            return await asyncio.to_thread(work.run)
        except asyncio.CancelledError:
            # The actual writer owns cleanup, not a cancellable asyncio proxy.
            # This still removes its result after final loop task cancellation.
            work.discard()
            raise

    def _export(self):
        # SQLite-Backup liest einen konsistenten Stand, während neue Eingänge
        # weiter geschrieben werden. Die exportierte Datei ist niemals öffentlich.
        with tempfile.TemporaryDirectory(prefix="ha-sauna-export-") as directory:
            snapshot = Path(directory) / "snapshot.sqlite"
            with (
                closing(sqlite3.connect(self.path)) as source,
                closing(sqlite3.connect(snapshot)) as target,
            ):
                source.backup(target)
            handle = tempfile.NamedTemporaryFile(
                prefix="ha-sauna-", suffix=".zip", delete=False
            )
            path = Path(handle.name)
            handle.close()
            try:
                with (
                    closing(sqlite3.connect(snapshot)) as db,
                    zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive,
                ):
                    db.row_factory = sqlite3.Row
                    archive.writestr(
                        "manifest.json",
                        encoded(
                            {
                                "schema": 1,
                                "entry_id": self.entry_id,
                                "resolution": "original_received",
                                "timestamps": "ISO-8601 with timezone",
                                "records": "append-only; session records preserve prior assignments",
                            }
                        ),
                    )
                    with archive.open("sessions.jsonl", "w") as out:
                        for row in db.execute(
                            "SELECT payload FROM sessions WHERE entry_id=? ORDER BY started_at",
                            (self.entry_id,),
                        ):
                            out.write((row[0] + "\n").encode())
                    with archive.open("records.jsonl", "w") as out:
                        for row in db.execute(
                            "SELECT * FROM records WHERE entry_id=? ORDER BY id",
                            (self.entry_id,),
                        ):
                            data = dict(row)
                            data["payload"] = json.loads(data["payload"])
                            out.write((encoded(data) + "\n").encode())
                    with archive.open("measurements.csv", "w") as raw:
                        out = io.TextIOWrapper(raw, encoding="utf-8", newline="")
                        writer = csv.writer(out)
                        writer.writerow(
                            (
                                "record_id",
                                "session_id",
                                "received_at",
                                "measured_at",
                                "position",
                                "quantity",
                                "source",
                                "value",
                                "raw_value",
                            )
                        )
                        for row in db.execute(
                            "SELECT * FROM records WHERE entry_id=? AND kind='measurement' ORDER BY id",
                            (self.entry_id,),
                        ):
                            data = json.loads(row["payload"])
                            writer.writerow(
                                (
                                    row["id"],
                                    row["session_id"],
                                    data["received_at"],
                                    data["measured_at"],
                                    data["position"],
                                    data["quantity"],
                                    data["source"],
                                    data["value"],
                                    data["raw_value"],
                                )
                            )
                        out.flush()
                        out.detach()
                return path
            except BaseException:
                path.unlink(missing_ok=True)
                raise
