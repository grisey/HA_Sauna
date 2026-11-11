"""Originalauflösung und nachvollziehbare Zustandsrevisionen in privatem SQLite."""

from __future__ import annotations

import asyncio
import concurrent.futures
import csv
import io
import json
import logging
import sqlite3
import threading
import time
import zipfile
from collections import OrderedDict, deque
from collections.abc import Mapping
from contextlib import closing, suppress
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from hashlib import sha256
from math import isfinite
from pathlib import Path

from .core import calibration_runs, recognition_archive
from .core.history import HISTORY_CONTEXT_SECONDS, measurement_window
from .core.key_figure_display import present_session, present_statistics
from .core.key_figures import (
    earlier_warmup,
    in_period,
    periods,
    program_kind,
    session_figures,
    session_input_from_archive,
    statistics_figures,
)
from .core.models import Energy, Position, Quantity, TimedPhase
from .core.parameters import BY_KEY, Parameters
from .core.phases import project_archive, project_session
from .core.recognition_control import recognition_control
from .history_humidity import project_absolute_humidity, read_originals
from .key_figure_parameters import door_sensor_lag

_LOGGER = logging.getLogger(__name__)
_CONFIGURATION_READ_CACHE_ENTRIES = 64
# IDs per context read of the history humidity, below every SQLite variable limit.
_CONTEXT_IDS_PER_READ = 500


def plain(value):
    if is_dataclass(value):
        # Energieintervalle reisen separat als Delta; Skalare kopieren keine Vollreihe.
        result = {
            field.name: plain(getattr(value, field.name))
            for field in fields(value)
            if not (isinstance(value, Energy) and field.name == "intervals")
        }
        if isinstance(value, TimedPhase):
            result.update(
                required_seconds=value.required_seconds,
                remaining_seconds=value.remaining_seconds,
            )
        return result
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


def _energy_value(value, key, default=None):
    return (
        value.get(key, default)
        if isinstance(value, Mapping)
        else getattr(value, key, default)
    )


def _energy_time(value):
    at = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(at, datetime) or at.utcoffset() is None:
        raise ValueError("Energiecursor benötigt einen Zeitpunkt mit Zeitzone")
    return at.astimezone(UTC)


def energy_interval_delta(energy, after=None):
    """Transport original contributions, clipping only their time boundaries.

    A missing interval field is legacy evidence, not a zero contribution. An
    invalid/outside cursor requests a complete bootstrap (``from=None``).
    The owner tuple is searched by end time without walking its saved prefix.
    """
    intervals = _energy_value(energy, "intervals")
    if not isinstance(intervals, (tuple, list)):
        return None
    try:
        end = _energy_time(_energy_value(energy, "accounted_at"))
        start = (
            _energy_time(_energy_value(intervals[0], "started_at"))
            if intervals else end
        )
    except (TypeError, ValueError, OverflowError):
        return None
    if start > end or (
        not intervals and any(
            _energy_value(energy, key, 0) != 0
            for key in ("measured_seconds", "estimated_seconds", "unknown_seconds")
        )
    ):
        return None
    try:
        cursor = _energy_time(after)
    except (TypeError, ValueError, OverflowError):
        cursor = None
    if cursor is not None and not start <= cursor <= end:
        cursor = None
    lower = cursor if cursor is not None else start
    first, last = 0, len(intervals)
    try:
        while first < last:
            middle = (first + last) // 2
            if _energy_time(_energy_value(intervals[middle], "ended_at")) <= lower:
                first = middle + 1
            else:
                last = middle
        originals = []
        covered = lower
        for index in range(first, len(intervals)):
            interval = intervals[index]
            original_start = _energy_time(_energy_value(interval, "started_at"))
            original_end = _energy_time(_energy_value(interval, "ended_at"))
            if original_start >= end:
                break
            clipped_start = max(original_start, lower)
            clipped_end = min(original_end, end)
            if clipped_start != covered or clipped_end <= clipped_start:
                return None
            original = plain(interval)
            original["started_at"] = clipped_start.isoformat()
            original["ended_at"] = clipped_end.isoformat()
            originals.append(original)
            covered = clipped_end
            if covered == end:
                break
    except (TypeError, ValueError, OverflowError):
        return None
    if covered != end:
        return None
    return {
        "from": cursor.isoformat() if cursor is not None else None,
        "to": end.isoformat(),
        "intervals": originals,
    }


def session_has_gangs(session):
    """Count the same confirmed rounds as the timeline, including interruption."""
    timeline = session.get("timeline", {})
    gangs = list(timeline.get("completed", ()))
    if timeline.get("active"):
        gangs.append(timeline["active"])
    return any(gang.get("infusion_events")
               or gang.get("recognition_kind") == "presence_confirmed" for gang in gangs)


class ExportUnavailable(RuntimeError):
    """The export cannot start now, or it was stopped before its last block."""


class _ExportStream(io.RawIOBase):
    """Non-seekable ZIP target that hands complete blocks over one at a time.

    The last written byte stays buffered until ``finish``. A ZIP stopped by an
    error therefore never ends with a complete end-of-central-directory record.
    """

    def __init__(self, put, size):
        super().__init__()
        self._put = put
        self._size = size
        self._buffer = bytearray()

    def writable(self):
        return True

    def write(self, data):
        self._buffer += data
        while len(self._buffer) > self._size:
            block = bytes(self._buffer[: self._size])
            del self._buffer[: self._size]
            self._put(block)
        return memoryview(data).nbytes

    def finish(self):
        if self._buffer:
            self._put(bytes(self._buffer))
            self._buffer.clear()


class ArchiveExport:
    """One ZIP export from a read transaction on the archive itself.

    ``async with archive.export(chunk_bytes) as export`` returns once the
    snapshot is fixed; every failure up to then precedes the first byte.
    ``async for block in export`` yields the ZIP. The worker waits until each
    block is taken. Leaving stops the worker and waits until its read
    transaction has ended.
    """

    def __init__(self, archive, chunk_bytes, check_only):
        self._archive = archive
        self._chunk_bytes = chunk_bytes
        self._check_only = check_only
        self._loop = None
        self._worker = None
        self._ready = None
        self._stop = threading.Event()
        self._reason = None
        self._accepted = None
        self._offer = None
        self._wakeup = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *_):
        await self.close()

    async def start(self):
        archive = self._archive
        self._loop = asyncio.get_running_loop()
        await archive.flush()
        # Same loop step as the backup's ownership in pre_backup: either the
        # backup sees and stops this export, or this export sees the backup.
        if archive._running_export is not None:
            raise ExportUnavailable("Ein Archivexport läuft bereits.")
        if not archive.resume.is_set() or archive.closed:
            raise ExportUnavailable("Das Archiv wird gerade gesichert oder geschlossen.")
        archive._running_export = self
        self._ready = self._loop.create_future()
        self._worker = self._loop.run_in_executor(None, self._produce)
        self._worker.add_done_callback(self._finished)
        try:
            await asyncio.wait((self._ready, self._worker),
                               return_when=asyncio.FIRST_COMPLETED)
            if not self._ready.done():
                self._worker.result()
                raise RuntimeError("Archivexport endete vor dem Lesestand")
        except BaseException:
            self.abort("Der Archivexport wurde vor dem Senden beendet.")
            raise

    def abort(self, reason):
        """Stop at the next block; a waiting worker is released immediately."""
        if self._reason is None:
            self._reason = reason
        self._stop.set()
        accepted = self._accepted
        if accepted is not None:
            # Already taken: the worker continues to its next stop check.
            with suppress(concurrent.futures.InvalidStateError):
                accepted.set_exception(ExportUnavailable(self._reason))
        self._offer = None
        self._wake()

    async def close(self, reason="Der Archivexport wurde beendet."):
        if self._worker is None:
            return
        if not self._worker.done():
            self.abort(reason)
        # Its error was raised by start or iteration, or results from abort.
        with suppress(Exception):
            await asyncio.shield(self._worker)

    def __aiter__(self):
        return self

    async def __anext__(self):
        while self._offer is None:
            if self._worker.done():
                self._worker.result()
                raise StopAsyncIteration
            self._wakeup = self._loop.create_future()
            try:
                await self._wakeup
            finally:
                self._wakeup = None
        block, accepted = self._offer
        self._offer = None
        accepted.set_result(None)
        return block

    def _wake(self):
        if self._wakeup is not None and not self._wakeup.done():
            self._wakeup.set_result(None)

    def _finished(self, worker):
        # Surfaced by start or iteration; a stopped export needs no extra report.
        worker.exception()
        if self._archive._running_export is self:
            self._archive._running_export = None
        self._wake()

    def _signal_ready(self):
        if not self._ready.done():
            self._ready.set_result(None)

    def _deliver(self, block, accepted):
        if accepted.done():
            return
        if self._stop.is_set():
            accepted.set_exception(ExportUnavailable(self._reason))
            return
        self._offer = (block, accepted)
        self._wake()

    def _put(self, block):
        # Publish before checking: abort either sees this future or the
        # worker sees the stop request.
        accepted = concurrent.futures.Future()
        self._accepted = accepted
        if self._stop.is_set():
            raise ExportUnavailable(self._reason)
        self._loop.call_soon_threadsafe(self._deliver, block, accepted)
        accepted.result()

    def _produce(self):
        with closing(sqlite3.connect(self._archive.path, isolation_level=None)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            # Outside WAL a long reader would block every archive write.
            if db.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                raise sqlite3.OperationalError("Archiv steht nicht im WAL-Modus")
            db.execute("BEGIN")
            try:
                # The first read fixes the snapshot; later commits stay invisible.
                db.execute("SELECT count(*) FROM sqlite_master").fetchone()
                self._loop.call_soon_threadsafe(self._signal_ready)
                if not self._check_only:
                    stream = _ExportStream(self._put, self._chunk_bytes)
                    self._archive._write_export(db, stream)
                    stream.finish()
            finally:
                db.execute("ROLLBACK")


class Archive:
    def __init__(self, path, entry_id, *, sessions_only=False, parameters=None,
                 trace_profile="normal"):
        if trace_profile not in ("normal", "analysis"):
            raise ValueError("Archivprofil muss normal oder analysis sein")
        # Recording policy only: historical records and reads stay unrestricted.
        self.trace_profile = trace_profile
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
        self.parameters = parameters if parameters is not None else Parameters({})
        self._discarded_sessions = set()
        self._energy_enqueued = {}
        self._projection_cache = OrderedDict()
        self._projection_lock = threading.Lock()
        self._projection_generation = 0
        self._projection_cache_entries = self.parameters.values["archive_projection_cache_entries"]
        # One evaluated revision per finished session; removed with the session.
        self._figure_cache = {}
        self._figure_lock = threading.Lock()
        # LZ2: Kennzahlen werden nacheinander gerechnet. Eine Anfrage während der
        # Vorbereitung nach dem Start wartet auf deren Ergebnis, statt es zu wiederholen.
        self._figure_compute_lock = threading.Lock()
        self.revision = 0
        self._context_pruned_before = None
        # At most one export holds a read transaction; the backup stops it.
        self._running_export = None
        self.start_seconds = None
        # K-Z2 (B4c): Temperaturmessungen der laufenden Sitzung, fortgeschrieben.
        self._difference_points = None

    async def start(self):
        began = time.monotonic()
        await asyncio.to_thread(self._initialize)
        # B4c: Startdauer des Archivs für Details · Zustand.
        self.start_seconds = time.monotonic() - began
        self.worker = asyncio.create_task(
            self._run(), name=f"ha_sauna_archive_{self.entry_id}"
        )

    def _initialize(self, now=None):
        now = now or datetime.now(UTC)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            metadata = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata'"
            ).fetchone()
            if metadata:
                schema = db.execute("SELECT value FROM metadata WHERE key='schema'").fetchone()
                if schema is None or schema[0] not in {"1", "2"}:
                    raise ValueError("Nicht unterstützte Archivversion")
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT OR IGNORE INTO metadata VALUES ('schema', '1');
                CREATE TABLE IF NOT EXISTS records (
                    id INTEGER PRIMARY KEY, entry_id TEXT NOT NULL, session_id TEXT,
                    kind TEXT NOT NULL, received_at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS records_session ON records(entry_id, session_id, id);
                DROP INDEX IF EXISTS records_measurement_clock;
                CREATE INDEX IF NOT EXISTS records_measurement_context ON records(entry_id, julianday(received_at))
                    WHERE kind='measurement';
                CREATE INDEX IF NOT EXISTS records_source_snapshot_context
                    ON records(entry_id, session_id, julianday(json_extract(payload,'$.received_at')), id)
                    WHERE kind='source_snapshot';
                CREATE INDEX IF NOT EXISTS records_consumer_events ON records(entry_id)
                    WHERE kind='consumer_event';
                CREATE INDEX IF NOT EXISTS records_energy_delta
                    ON records(entry_id, session_id, json_extract(payload,'$.energy.interval_delta.to'), id)
                    WHERE kind='session';
                CREATE INDEX IF NOT EXISTS records_energy_bootstrap
                    ON records(entry_id, session_id, id)
                    WHERE kind='session' AND json_type(payload,'$.energy.interval_delta.from')='null';
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY, entry_id TEXT NOT NULL,
                    started_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    ended_at TEXT, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS consumer_receipts (
                    entry_id TEXT NOT NULL, event_id TEXT NOT NULL,
                    PRIMARY KEY(entry_id,event_id));
            """)
            # Additive migration: historical inline payloads and IDs stay intact.
            # The version table, both nullable references and schema marker commit
            # together, including when upgrading an existing schema-1 database.
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS configuration_versions (
                id INTEGER PRIMARY KEY, entry_id TEXT NOT NULL,
                digest TEXT NOT NULL, payload TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS configuration_digest "
                       "ON configuration_versions(entry_id,digest)")
            db.execute("""CREATE TRIGGER IF NOT EXISTS configuration_immutable
                BEFORE UPDATE ON configuration_versions
                BEGIN SELECT RAISE(ABORT, 'Configuration versions are immutable'); END""")
            for table in ("records", "sessions"):
                columns = {column[1] for column in db.execute(f"PRAGMA table_info({table})")}
                if "configuration_id" not in columns:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN configuration_id INTEGER "
                               "REFERENCES configuration_versions(id)")
                db.execute(f"CREATE INDEX IF NOT EXISTS {table}_configuration "
                           f"ON {table}(configuration_id) WHERE configuration_id IS NOT NULL")
            db.execute("UPDATE metadata SET value='2' WHERE key='schema'")
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
                    if not self._retained(db, row[0], json.loads(row[1])):
                        self._delete_session(db, row[0])
                db.execute(
                    "DELETE FROM records WHERE entry_id=? AND "
                    "((session_id IS NULL AND kind!='measurement') OR "
                    "session_id NOT IN (SELECT session_id FROM sessions WHERE entry_id=?))",
                    (self.entry_id, self.entry_id),
                )
                self._prune_context(db, now)
                self._prune_configurations(db)
                if db.total_changes > before_cleanup:
                    self.revision = self._bump_revision(db)

    def _retained(self, db, session_id, payload):
        """A confirmed gang or a calibration protocol keeps a finished session (E-15)."""
        return session_has_gangs(payload) or db.execute(
            "SELECT 1 FROM records WHERE entry_id=? AND session_id=? "
            "AND kind='calibration' LIMIT 1",
            (self.entry_id, session_id),
        ).fetchone() is not None

    def _configuration_column(self, db):
        """Permit read-only access to an unmodified schema-1 archive as well."""
        # Inspect this read snapshot, not mutable instance state: another owner
        # may have upgraded the database since an earlier read-only access.
        columns = {column[1] for column in db.execute("PRAGMA table_info(records)")}
        return ",configuration_id" if "configuration_id" in columns else ""

    def _configuration_version(self, db, configuration):
        # Canonical bytes preserve JSON types and values; key order is not a
        # configuration change. A digest narrows the lookup, never proves equality.
        payload = json.dumps(configuration, ensure_ascii=False, allow_nan=False,
                             sort_keys=True, separators=(",", ":"))
        digest = sha256(payload.encode("utf-8")).hexdigest()
        for identity, original in db.execute(
            "SELECT id,payload FROM configuration_versions WHERE entry_id=? AND digest=?",
            (self.entry_id, digest),
        ):
            if original == payload:
                return identity
        return db.execute(
            "INSERT INTO configuration_versions(entry_id,digest,payload) VALUES(?,?,?)",
            (self.entry_id, digest, payload),
        ).lastrowid

    def _decode_payload(self, db, payload, configuration_id=None, *, cache=None):
        data = json.loads(payload)
        if configuration_id is None:
            return data
        if "configuration" in data:
            raise ValueError("Widersprüchliche Archivkonfiguration")
        # A bounded per-read cache avoids one SQL lookup per repeated snapshot,
        # including alternating versions. Each result owns its decoded object.
        original = cache.get(configuration_id) if cache is not None else None
        if original is None:
            row = db.execute(
                "SELECT payload FROM configuration_versions WHERE id=? AND entry_id=?",
                (configuration_id, self.entry_id),
            ).fetchone()
            if row is None:
                raise ValueError("Fehlende Archivkonfigurationsversion")
            original = row[0]
            if cache is not None:
                cache[configuration_id] = original
                if len(cache) > _CONFIGURATION_READ_CACHE_ENTRIES:
                    cache.popitem(last=False)
        elif cache is not None:
            cache.move_to_end(configuration_id)
        data["configuration"] = json.loads(original)
        return data

    def _decode_record(self, db, row, *, cache=None):
        record = dict(row)
        identity = record.pop("configuration_id", None)
        record["payload"] = self._decode_payload(db, record["payload"], identity, cache=cache)
        return record

    def _prune_configurations(self, db):
        # User-authorized deletion removes settings only after their last
        # retained historical or current reference disappears.
        db.execute("""DELETE FROM configuration_versions WHERE entry_id=?
            AND NOT EXISTS (SELECT 1 FROM records WHERE configuration_id=configuration_versions.id)
            AND NOT EXISTS (SELECT 1 FROM sessions WHERE configuration_id=configuration_versions.id)""",
                   (self.entry_id,))

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
        if self.closed:
            raise RuntimeError("Archiv ist geschlossen")
        if session.session_id in self._discarded_sessions:
            return
        payload = plain(session)
        payload["configuration"] = plain(configuration)
        previous = self._energy_enqueued.get(session.session_id)
        delta = energy_interval_delta(session.energy, after=previous)
        if delta is not None:
            accounted_at = _energy_time(delta["to"])
            if previous is not None and accounted_at < previous:
                raise ValueError("Archivierte Energiezeit darf nicht rückwärts laufen")
            payload["energy"]["interval_delta"] = delta
        self._append_plain("session", at, payload, session.session_id)
        # The frozen queue item, including retries, owns this contribution now.
        # Encoding/enqueue failure must leave the preceding watermark intact.
        if delta is not None:
            self._energy_enqueued[session.session_id] = accounted_at

    async def _run(self):
        while True:
            kind, payload = await self.queue.get()
            try:
                if kind == "stop":
                    return
                future = payload[-1] if kind in {"erase", "label"} else payload
                if kind in {"fence", "pause", "erase", "label"} and future.cancelled():
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
                    elif kind in {"fence", "pause", "erase", "label"} and not future.cancelled():
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
                elif kind == "label":
                    result = await asyncio.to_thread(self._label, *payload[:-1])
                    if not future.cancelled():
                        future.set_result(result)
                elif kind == "pause":
                    await asyncio.to_thread(self._checkpoint_backup)
                self.failure = None
                if kind == "fence":
                    payload.set_result(None)
                elif kind == "pause" and not payload.cancelled():
                    payload.set_result(None)
                    await self.resume.wait()
            except Exception as error:
                if kind not in {"erase", "label"} or not isinstance(error, (KeyError, ValueError)):
                    self._report_failure(error)
                if kind == "record":
                    self.failed_records.append(payload)
                elif kind in {"fence", "pause", "erase", "label"} and not future.cancelled():
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
                self._report_failure(error)
                return False
            self.failed_records.popleft()
        return True

    def _report_failure(self, error):
        if (type(error), str(error)) != (type(self.failure), str(self.failure)):
            _LOGGER.error(
                "Saunaarchiv konnte nicht geschrieben werden (%s): %s",
                type(error).__name__, error,
                exc_info=(type(error), error, error.__traceback__),
            )
        self.failure = error

    def _checkpoint_backup(self):
        # A paused writer must leave a standalone main database for HA's file
        # backup. Never acknowledge the pause while committed pages remain in
        # a WAL held by an older reader.
        with closing(sqlite3.connect(self.path)) as db:
            busy, _, _ = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if busy:
                raise sqlite3.OperationalError("Archiv-Backup durch aktiven Leser blockiert")

    def _write(self, record):
        kind, at, payload, session_id = record
        if session_id in self._discarded_sessions:
            return
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            if kind == "consumer_receipt":
                db.execute("INSERT OR IGNORE INTO consumer_receipts VALUES(?,?)",
                           (self.entry_id, json.loads(payload)["event_id"]))
                return
            if kind == "session" and self.sessions_only:
                data = json.loads(payload)
                if data.get("ended_at") and not self._retained(db, session_id, data):
                    self._delete_session(db, session_id)
                    self._prune_context(db, datetime.fromisoformat(at))
                    self._prune_configurations(db)
                    revision = self._bump_revision(db)
                    db.commit()
                    self.revision = revision
                    self._discarded_sessions.add(session_id)
                    self._invalidate_projection(session_id)
                    return
            configuration_id = None
            if kind == "session":
                data = json.loads(payload)
                if "configuration" in data:
                    configuration_id = self._configuration_version(db, data.pop("configuration"))
                    payload = encoded_plain(data)
            db.execute(
                "INSERT INTO records(entry_id,session_id,kind,received_at,payload,configuration_id) "
                "VALUES(?,?,?,?,?,?)",
                (self.entry_id, session_id, kind, at, payload, configuration_id),
            )
            if kind == "session":
                energy = data.get("energy")
                session_payload = payload
                if isinstance(energy, dict):
                    has_transport = any(key in energy for key in (
                        "intervals", "interval_delta", "intervals_available",
                    ))
                    delta = energy.pop("interval_delta", None)
                    energy.pop("intervals", None)
                    if delta is not None:
                        energy["intervals_available"] = True
                    else:
                        energy.pop("intervals_available", None)
                    if has_transport:
                        session_payload = encoded_plain(data)
                # This scalar snapshot and the original delta above commit
                # together; repeated snapshots never copy the interval prefix.
                db.execute(
                    """INSERT INTO sessions(session_id,entry_id,started_at,updated_at,ended_at,payload,configuration_id)
                    VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(session_id) DO UPDATE SET updated_at=excluded.updated_at,
                    ended_at=excluded.ended_at,payload=excluded.payload,
                    configuration_id=excluded.configuration_id""",
                    (
                        session_id,
                        self.entry_id,
                        data["timeline"]["session_started_at"],
                        at,
                        data["ended_at"],
                        session_payload,
                        configuration_id,
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
        self._energy_enqueued.pop(session_id, None)

    def _prune_context(self, db, now, *, incremental=False):
        """Revisit newly expired buffer points; full scans only at lifecycle changes."""
        cutoff = now - timedelta(seconds=self.parameters.values["history_context_seconds"])
        previous = self._context_pruned_before if incremental else None
        if previous is not None and cutoff <= previous:
            return
        lower_bound = " AND julianday(received_at)>=julianday(?)" if previous else ""
        candidates = db.execute(
            """SELECT id,julianday(received_at),json_extract(payload,'$.position'),
            json_extract(payload,'$.quantity'),json_extract(payload,'$.source')
            FROM records WHERE entry_id=? AND session_id IS NULL
            AND kind='measurement' AND julianday(received_at)<julianday(?)""" + lower_bound,
            (self.entry_id, cutoff.isoformat(), *((previous.isoformat(),) if previous else ())),
        ).fetchall()
        self._context_pruned_before = cutoff if incremental else None
        if not candidates:
            return
        # Session settings are decoded once per session, not once per buffered point.
        positions, quantities = tuple(Position), tuple(Quantity)
        roles = [f"{position}_{quantity}" for position in positions for quantity in quantities]
        context = """COALESCE(json_extract(c.payload,'$.parameters.history_context_seconds'),
            json_extract(s.payload,'$.configuration.parameters.history_context_seconds'),?)/86400.0"""
        windows = []
        for first, last, started, ended, *sources in db.execute(
            f"""SELECT julianday(s.started_at)-{context},
            julianday(COALESCE(s.ended_at, s.updated_at))+{context},
            julianday(s.started_at),julianday(COALESCE(s.ended_at, s.updated_at)),"""
            + ",".join(f"""COALESCE(json_extract(c.payload,'$.bindings.{key}'),
                json_extract(s.payload,'$.configuration.bindings.{key}'))""" for key in roles)
            + """ FROM sessions s LEFT JOIN configuration_versions c
                ON c.id=s.configuration_id AND c.entry_id=s.entry_id
            WHERE s.entry_id=?""",
            (HISTORY_CONTEXT_SECONDS, HISTORY_CONTEXT_SECONDS, self.entry_id),
        ):
            # A session without a valid time provides no context.
            if None not in (first, last, started, ended):
                windows.append((first, last, started, ended, dict(zip(roles, sources))))
        db.executemany("DELETE FROM records WHERE id=?", [
            (identity,) for identity, received, position, quantity, source in candidates
            if not (
                position in positions and quantity in quantities and source is not None
                and any(first <= received <= last and (received <= started or received >= ended)
                        and bound[f"{position}_{quantity}"] == source
                        for first, last, started, ended, bound in windows)
            )
        ])

    def _bump_revision(self, db):
        revision = self.revision + 1
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('revision', ?)",
                   (str(revision),))
        return revision

    def _erase(self, session_id, reset, now=None):
        now = now or datetime.now(UTC)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("PRAGMA foreign_keys=ON")
            if reset:
                ids = [row[0] for row in db.execute(
                    "SELECT session_id FROM sessions WHERE entry_id=?", (self.entry_id,)
                )]
                db.execute("DELETE FROM records WHERE entry_id=?", (self.entry_id,))
                db.execute("DELETE FROM sessions WHERE entry_id=?", (self.entry_id,))
                db.execute("DELETE FROM consumer_receipts WHERE entry_id=?", (self.entry_id,))
                self._energy_enqueued.clear()
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
            self._prune_configurations(db)
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

    async def label_session(self, session_id, test, at, user_id=None):
        """Testbetrieb (G25): eine abgeschlossene Sitzung kennzeichnen oder die Kennzeichnung
        aufheben. Der Eintrag ``session_label`` ergänzt die Sitzung; der Originalbestand bleibt."""
        if self.closed:
            raise RuntimeError("Archiv ist geschlossen")
        future = asyncio.get_running_loop().create_future()
        self.queue.put_nowait(("label", (session_id, bool(test), at, user_id, future)))
        return await asyncio.shield(future)

    def _label(self, session_id, test, at, user_id):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute(
                "SELECT ended_at FROM sessions WHERE entry_id=? AND session_id=?",
                (self.entry_id, session_id),
            ).fetchone()
        if row is None:
            raise KeyError(session_id)
        if row[0] is None:
            raise ValueError("Testbetrieb lässt sich erst nach Abschluss der Sitzung kennzeichnen.")
        self._write(("session_label", at.isoformat(),
                     encoded_plain({"test": test, "user_id": user_id}), session_id))
        with closing(sqlite3.connect(self.path)) as db, db:
            revision = self._bump_revision(db)
            db.commit()
        self.revision = revision
        self._invalidate_projection(session_id)
        return test

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
            await self._stop_export()
            await asyncio.to_thread(self._checkpoint_backup)
            return
        if not self.resume.is_set():
            raise RuntimeError("Archiv wird bereits gesichert")
        # Establish ownership before enqueueing: a release while the writer is
        # catching up must not be lost when it eventually reaches the pause.
        self.resume.clear()
        try:
            await self._stop_export()
            await self._barrier("pause")
        except BaseException:
            self.release_backup()
            raise

    async def _stop_export(self):
        # The backup takes precedence: its checkpoint needs an empty read view.
        export = self._running_export
        if export is not None:
            await export.close(
                "Der Archivexport wurde für ein Home-Assistant-Backup abgebrochen."
            )

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

    def read(
        self, session_id=None, *, after=0, limit=None, kinds=None, now=None,
        humidity_display=False, energy_after=None, humidity_values=False,
    ):
        if limit is None:
            limit = self.parameters.values["archive_read_page_records"]
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
            configuration_column = self._configuration_column(db)
            configuration_cache = OrderedDict()
            row = db.execute(
                "SELECT payload,updated_at" + configuration_column
                + " FROM sessions WHERE entry_id=? AND session_id=?",
                (self.entry_id, session_id),
            ).fetchone()
            if row is None:
                return None
            kind_filter = (
                " AND kind IN (" + ",".join("?" for _ in kinds) + ")"
                if kinds is not None else ""
            )
            session = self._decode_payload(
                db, row["payload"], row["configuration_id"] if configuration_column else None,
                cache=configuration_cache,
            )
            energy_delta = self._read_energy_delta(db, session_id, session, energy_after)
            if energy_delta is not None:
                session["energy"]["interval_delta"] = energy_delta
            current = now or datetime.now(UTC)
            started = datetime.fromisoformat(session["timeline"]["session_started_at"])
            ended = datetime.fromisoformat(session["ended_at"]) if session.get("ended_at") else None
            context_definition = BY_KEY["history_context_seconds"]
            configuration = session.get("configuration")
            configuration = configuration if isinstance(configuration, Mapping) else {}
            context = configuration.get("parameters", {}).get(
                context_definition.key, context_definition.default
            )
            context = context_definition.validate(context)
            window = measurement_window(started, ended, current, context_seconds=context)
            bindings = configuration.get("bindings", {})
            sources = tuple(
                (bindings[key], *key.split("_", 1)) for key in (
                    "upper_temperature", "upper_humidity", "lower_temperature", "lower_humidity"
                ) if key in bindings
            )
            # Keep session and context lookups as separate indexed streams.
            # An OR over session_id and JSON predicates makes SQLite walk
            # unrelated retained records again for every page. Union IDs
            # first so only this page's original payloads are materialized.
            streams = [
                "SELECT id FROM records WHERE entry_id=? AND session_id=? AND id>?"
                + kind_filter
            ]
            stream_args = [self.entry_id, session_id, after, *(kinds or ())]
            if sources:
                source_filter = (
                    " AND ("
                    + " OR ".join(
                        "(json_extract(payload,'$.source')=? AND json_extract(payload,'$.position')=? "
                        "AND json_extract(payload,'$.quantity')=?)" for _ in sources
                    ) + ")"
                )
                # The middle of the session is already covered by its own
                # stream. Both inclusive edges retain the old boundary rule;
                # UNION removes records present in more than one stream.
                if kinds is None or "measurement" in kinds:
                    for first, last in (
                        (window["started_at"], started.isoformat()),
                        ((ended or current).isoformat(), window["ended_at"]),
                    ):
                        streams.append(
                            "SELECT id FROM records INDEXED BY records_measurement_context "
                            "WHERE entry_id=? AND kind='measurement' "
                            "AND julianday(received_at) BETWEEN julianday(?) AND julianday(?) "
                            "AND id>?" + source_filter
                        )
                        stream_args.extend((self.entry_id, first, last, after))
                        stream_args.extend(value for source in sources for value in source)
            records = db.execute(
                "SELECT * FROM records WHERE id IN ("
                + " UNION ".join(streams) + " ORDER BY id LIMIT ?) ORDER BY id",
                (*stream_args, limit),
            ).fetchall()
            page_records = [
                self._decode_record(db, record, cache=configuration_cache)
                for record in records
            ]
            humidity = None
            if humidity_values:
                # LZ4: display values beside the unchanged page records.
                humidity = self._humidity_display(
                    db, session, page_records, sources, attach=False
                )
            elif humidity_display:
                page_records = self._humidity_display(
                    db, session, page_records, sources
                )
            if session.get("base_phases"):
                projection = project_session(session, row["updated_at"])
            else:
                projection = self._legacy_projection(
                    db, session_id, session, row, generation=generation
                )
            result = {
                "session": session,
                "measurement_window": window,
                "phase_projection": plain(projection),
                "records": page_records,
                "next_after": records[-1]["id"] if len(records) == limit else None,
            }
            if humidity is not None:
                # Aligned with ``records``; ``None`` for kinds without a display.
                result["humidity_displays"] = humidity
            return result

    def _read_energy_delta(self, db, session_id, session, after=None):
        """Read only contributing deltas in the scalar row's SQLite snapshot."""
        energy = session.get("energy", {})
        if energy.get("intervals_available") is not True:
            return None
        try:
            end = _energy_time(energy.get("accounted_at"))
        except (TypeError, ValueError, OverflowError):
            return None
        try:
            cursor = _energy_time(after)
        except (TypeError, ValueError, OverflowError):
            cursor = None
        # Common status/paging request: the scalar horizon has not advanced.
        # No prefix, original record, or bootstrap needs to be read in this case.
        if cursor == end:
            return {"from": end.isoformat(), "to": end.isoformat(), "intervals": []}
        snapshot_id = db.execute(
            "SELECT MAX(id) FROM records INDEXED BY records_energy_delta "
            "WHERE entry_id=? AND session_id=? AND kind='session' "
            "AND json_extract(payload,'$.energy.interval_delta.to')=?",
            (self.entry_id, session_id, end.isoformat()),
        ).fetchone()[0]
        if snapshot_id is None:
            return None
        bootstrap = db.execute(
            "SELECT id, COALESCE(json_extract(payload,'$.energy.interval_delta.intervals[0].started_at'), "
            "json_extract(payload,'$.energy.interval_delta.to')) AS started_at "
            "FROM records INDEXED BY records_energy_bootstrap "
            "WHERE entry_id=? AND session_id=? AND kind='session' AND id<=? "
            "AND json_type(payload,'$.energy.interval_delta.from')='null' "
            "ORDER BY id DESC LIMIT 1",
            (self.entry_id, session_id, snapshot_id),
        ).fetchone()
        if bootstrap is None:
            return None
        try:
            start = _energy_time(bootstrap["started_at"])
        except (TypeError, ValueError, OverflowError):
            return None
        if cursor is not None and not start <= cursor <= end:
            cursor = None
        cutoff = (
            " AND json_extract(payload,'$.energy.interval_delta.to')>?"
            if cursor is not None else ""
        )
        rows = db.execute(
            "SELECT json_extract(payload,'$.energy.interval_delta') FROM records "
            "INDEXED BY records_energy_delta WHERE entry_id=? AND session_id=? "
            "AND kind='session' AND id>=? AND id<=?" + cutoff + " ORDER BY id",
            (self.entry_id, session_id, bootstrap["id"], snapshot_id,
             *((cursor.isoformat(),) if cursor is not None else ())),
        )
        originals = []
        covered = cursor if cursor is not None else start
        for (raw,) in rows:
            if raw is None:
                continue
            delta = json.loads(raw)
            for interval in delta["intervals"]:
                first = _energy_time(interval["started_at"])
                last = min(_energy_time(interval["ended_at"]), end)
                if last <= covered:
                    continue
                first = max(first, covered)
                if first != covered or last <= first:
                    return None
                original = {
                    **interval, "started_at": first.isoformat(), "ended_at": last.isoformat(),
                }
                if (
                    originals and originals[-1]["ended_at"] == original["started_at"]
                    and originals[-1].get("source") == original.get("source")
                    and originals[-1].get("power_kw") == original.get("power_kw")
                ):
                    originals[-1]["ended_at"] = original["ended_at"]
                else:
                    originals.append(original)
                covered = last
        if covered != end:
            return None
        return {
            "from": cursor.isoformat() if cursor is not None else None,
            "to": end.isoformat(),
            "intervals": originals,
        }

    def _humidity_display(self, db, session, records, sources, *, attach=True):
        """Read only this page's fresh source context in its SQLite snapshot.

        Each page original is read once; the reads give the freshness windows
        and the projection. Without ``attach`` the display values are returned
        aligned with ``records`` instead of projected copies.
        """
        configuration = session.get("configuration")
        configuration = configuration if isinstance(configuration, Mapping) else {}
        bindings = configuration.get("bindings", {})
        timeout = configuration.get("parameters", {}).get(
            "sensor_timeout_seconds", self.parameters.values["sensor_timeout_seconds"]
        )
        context = {}
        valid_timeout = (
            not isinstance(timeout, bool)
            and isinstance(timeout, (int, float))
            and isfinite(timeout)
            and timeout >= 0
        )
        reads = read_originals(records, bindings)
        received = []
        if valid_timeout and sources:
            source_roles = set(sources)
            for record, original in zip(records, reads):
                if original is not None:
                    # A read original has a bound source and its zoned UTC reception.
                    received.append(original[0])
                    continue
                if record["kind"] not in {"measurement", "source_snapshot"}:
                    continue
                payload = record["payload"]
                if not isinstance(payload, Mapping):
                    continue
                source_role = (
                    payload.get("source"), payload.get("position"),
                    payload.get("quantity"),
                )
                if (
                    not all(isinstance(value, str) for value in source_role)
                    or source_role not in source_roles
                ):
                    continue
                try:
                    at = datetime.fromisoformat(payload["received_at"])
                except (KeyError, TypeError, ValueError):
                    continue
                if at.utcoffset() is not None:
                    received.append(at.astimezone(UTC))
        windows = []
        if received:
            try:
                ttl = timedelta(seconds=timeout)
                for at in sorted(received):
                    first = at - ttl
                    if windows and first <= windows[-1][1]:
                        windows[-1] = (windows[-1][0], at)
                    else:
                        windows.append((first, at))
            except OverflowError:
                windows = []
        if windows:
            page_ids = {record["id"] for record in records}
            last_id = max(page_ids)
            source_filter = (
                " AND ("
                + " OR ".join(
                    "(json_extract(payload,'$.source')=? "
                    "AND json_extract(payload,'$.position')=? "
                    "AND json_extract(payload,'$.quantity')=?)" for _ in sources
                ) + ")"
            )
            source_values = tuple(value for source in sources for value in source)
            # Merge only overlapping freshness windows. An old copied source
            # must not load the retained history between it and newer inputs.
            # The time windows read only IDs from their indexes; payloads and
            # the source test follow for originals outside this page, which in
            # archive order are those before it.
            candidates = {}
            for first, last in windows:
                # Measurements use their actual received clock. Snapshots keep
                # their original payload clock instead of refreshing a source.
                queries = (
                    (
                        (
                            "SELECT id FROM records INDEXED BY records_measurement_context "
                            "WHERE entry_id=? AND kind='measurement' AND id<=? "
                            "AND julianday(received_at) BETWEEN julianday(?) AND julianday(?)"
                        ),
                        (self.entry_id, last_id, first.isoformat(), last.isoformat()),
                    ),
                    (
                        # The partial payload-clock index bounds snapshot context
                        # without walking all prior diagnostic IDs on every page.
                        (
                            "SELECT id FROM records INDEXED BY records_source_snapshot_context "
                            "WHERE entry_id=? AND session_id=? AND kind='source_snapshot' "
                            "AND id<=? AND julianday(json_extract(payload,'$.received_at')) "
                            "BETWEEN julianday(?) AND julianday(?)"
                        ),
                        (self.entry_id, session["timeline"]["session_id"], last_id,
                         first.isoformat(), last.isoformat()),
                    ),
                )
                for query, arguments in queries:
                    for (identity,) in db.execute(query, arguments):
                        if identity not in page_ids:
                            candidates[identity] = None
            identities = list(candidates)
            for start in range(0, len(identities), _CONTEXT_IDS_PER_READ):
                chunk = identities[start:start + _CONTEXT_IDS_PER_READ]
                # NOT INDEXED keeps direct ID lookups; without statistics SQLite
                # would otherwise walk every record of the entry for many IDs.
                for record in db.execute(
                    "SELECT * FROM records NOT INDEXED WHERE entry_id=? AND id IN ("
                    + ",".join("?" for _ in chunk) + ")" + source_filter,
                    (self.entry_id, *chunk, *source_values),
                ):
                    context[record["id"]] = self._decode_record(db, record)
        return project_absolute_humidity(
            records, context=context.values(), bindings=bindings,
            timeout_seconds=timeout, reads=reads, attach=attach,
        )

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
                    row["configuration_id"] if "configuration_id" in row.keys() else None,
                    revision[0] if revision else None)
        with self._projection_lock:
            current = generation == self._projection_generation
            cached = self._projection_cache.get(session_id) if current else None
            if cached is not None and cached[0] == identity:
                self._projection_cache.move_to_end(session_id)
                return cached[1]
            # Legacy snapshots need all phase/session evidence and heater
            # contact reports once per saved state, independently of pagination.
            # Other source roles are not consumed by project_archive; filter
            # them before materializing/decoding their original payloads.
            configuration_column = self._configuration_column(db)
            configuration_cache = OrderedDict()
            evidence = (
                {
                    "kind": r["kind"],
                    "received_at": r["received_at"],
                    "payload": self._decode_payload(
                        db, r["payload"], r["configuration_id"] if "configuration_id" in r.keys() else None,
                        cache=configuration_cache,
                    ),
                }
                for r in db.execute(
                    "SELECT kind,received_at,payload" + configuration_column
                    + " FROM records WHERE entry_id=? AND session_id=? "
                    "AND (kind IN ('phase','session') OR (kind='source_state' "
                    "AND json_extract(payload,'$.role')='heater')) ORDER BY id",
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

    def _key_figure_sessions(self, db):
        """Finished sessions with timeline and their revision identity, oldest first.

        A new session revision changes ``updated_at`` or the last record ID; the
        identity of a session without its own settings includes the session that
        supplies them (Q-P of the key figure catalogue).
        """
        stored = "json_type(s.payload,'$.configuration') IS NOT NULL"
        configuration, own = (
            ("s.configuration_id", f"(s.configuration_id IS NOT NULL OR {stored})")
            if self._configuration_column(db) else ("NULL", f"({stored})")
        )
        rows = db.execute(
            f"SELECT s.session_id,s.started_at,s.ended_at,s.updated_at,{configuration},{own},"
            "(SELECT MAX(id) FROM records r WHERE r.entry_id=s.entry_id "
            "AND r.session_id=s.session_id) FROM sessions s WHERE s.entry_id=? "
            "AND s.ended_at IS NOT NULL AND json_type(s.payload,'$.timeline')='object' "
            "ORDER BY s.started_at",
            (self.entry_id,),
        ).fetchall()
        result = []
        for index, (identity, started, ended, updated, configuration, own, last) in enumerate(rows):
            source = identity if own else next(
                (later[0] for later in rows[index + 1:] if later[5]), None
            )
            revision = (updated, last, configuration)
            if source is not None and source != identity:
                later = next(row for row in rows if row[0] == source)
                revision = (*revision, source, later[3], later[4])
            result.append({
                "session_id": identity, "started_at": started, "ended_at": ended,
                "parameter_session": source, "revision": revision,
            })
        return result

    def _key_figure_input(self, db, entry, *, volume_m3, price_eur_per_kwh):
        column = self._configuration_column(db)

        def payload(session_id):
            row = db.execute(
                "SELECT payload" + column + " FROM sessions WHERE entry_id=? AND session_id=?",
                (self.entry_id, session_id),
            ).fetchone()
            return self._decode_payload(db, row[0], row[1] if column else None)

        session_id = entry["session_id"]
        session = payload(session_id)
        source = entry["parameter_session"]
        settings = session if source == session_id else (
            payload(source) if source is not None else {}
        )

        def records(kind):
            return [json.loads(raw) for (raw,) in db.execute(
                "SELECT payload FROM records WHERE entry_id=? AND session_id=? AND kind=? "
                "ORDER BY id", (self.entry_id, session_id, kind),
            )]

        recorded = db.execute(
            "SELECT 1 FROM records WHERE entry_id=? AND session_id=? "
            "AND kind='recognition_state' LIMIT 1", (self.entry_id, session_id),
        ).fetchone() is not None
        return session_input_from_archive(
            session,
            ended_at=entry["ended_at"],
            measurements=db.execute(
                "SELECT received_at,json_extract(payload,'$.position'),"
                "json_extract(payload,'$.quantity'),json_extract(payload,'$.value') "
                "FROM records WHERE entry_id=? AND session_id=? AND kind='measurement' "
                "ORDER BY id", (self.entry_id, session_id),
            ).fetchall(),
            detections=records("detection"),
            # AF: nur Sitzungen mit archivierten Entscheidungen „Abklingen abgeflacht“
            decays=records("recognition_decay") or None,
            environment=[(received_at, json.loads(raw)) for received_at, raw in db.execute(
                "SELECT received_at,payload FROM records WHERE entry_id=? AND session_id=? "
                "AND kind='environment' ORDER BY id", (self.entry_id, session_id),
            )],
            # K-E2 only for sessions recorded with the recognition state history
            rejected=records("recognition_rejected") if recorded else None,
            episodes=records("recognition_episode"),
            configuration=settings.get("configuration"),
            parameter_origin=(
                "session" if source == session_id else "next_session" if source else None
            ),
            volume_m3=volume_m3,
            price_eur_per_kwh=price_eur_per_kwh,
            door_sensor_lag=door_sensor_lag(),
        )

    def _test_label(self, db, session_id):
        """Testbetrieb (G25): letzte Kennzeichnung von Hand, sonst automatisch bei einem
        Kalibrierprotokoll (Nutzer 10.11.)."""
        row = db.execute(
            "SELECT json_extract(payload,'$.test') FROM records WHERE entry_id=? "
            "AND session_id=? AND kind='session_label' ORDER BY id DESC LIMIT 1",
            (self.entry_id, session_id),
        ).fetchone()
        if row is not None:
            return {"test": bool(row[0]), "test_source": "user"}
        calibrated = db.execute(
            "SELECT 1 FROM records WHERE entry_id=? AND session_id=? AND kind='calibration' "
            "LIMIT 1", (self.entry_id, session_id),
        ).fetchone() is not None
        return {"test": calibrated, "test_source": "calibration" if calibrated else None}

    def key_figures(self, *, volume_m3=None, price_eur_per_kwh=None, stop=None):
        """Raw key figures of all finished sessions, oldest first.

        Each session's result is cached until its revision or the user settings
        for volume and electricity price change. A session whose archived data
        cannot be evaluated is returned with ``figures`` set to ``None``.
        ``stop`` ends the evaluation before the next uncached session (LZ2,
        preparation); the result then covers only the sessions before it.
        """
        settings = (volume_m3, price_eur_per_kwh)
        with self._figure_compute_lock, closing(sqlite3.connect(self.path)) as db:
            db.execute("BEGIN")
            entries = self._key_figure_sessions(db)
            result = []
            for entry in entries:
                identity = (entry["revision"], settings)
                with self._figure_lock:
                    cached = self._figure_cache.get(entry["session_id"])
                if cached is not None and cached[0] == identity:
                    figures, label = cached[1], cached[2]
                elif stop is not None and stop():
                    break
                else:
                    try:
                        figures = session_figures(self._key_figure_input(
                            db, entry, volume_m3=volume_m3,
                            price_eur_per_kwh=price_eur_per_kwh,
                        ))
                    except (KeyError, TypeError, ValueError, AttributeError) as error:
                        _LOGGER.debug("Kennzahlen für %s nicht auswertbar: %s",
                                      entry["session_id"], error)
                        figures = None
                    # Eine neue Kennzeichnung ändert die letzte Eintrags-ID und damit
                    # die Revision; sie wird mit den Kennzahlen zwischengespeichert.
                    label = self._test_label(db, entry["session_id"])
                    with self._figure_lock:
                        self._figure_cache[entry["session_id"]] = (identity, figures, label)
                result.append({**entry, **label, "figures": figures})
        with self._figure_lock:
            current = {entry["session_id"] for entry in entries}
            for session_id in set(self._figure_cache) - current:
                del self._figure_cache[session_id]
        return result

    def prepare_key_figures(self, *, volume_m3=None, price_eur_per_kwh=None):
        """LZ2: Kennzahlen aller abgeschlossenen Sitzungen in den Zwischenspeicher rechnen.

        Läuft nach dem Start im Hintergrund, damit der erste Abruf von Statistik oder
        Sitzungskennzahlen nicht alle Sitzungen rechnen muss. Endet vor der nächsten
        Sitzung, sobald das Archiv geschlossen oder für ein Backup angehalten wird; ihr
        Lesevorgang hält den Backup-Checkpoint dann höchstens eine Sitzung lang auf.
        """
        self.key_figures(volume_m3=volume_m3, price_eur_per_kwh=price_eur_per_kwh,
                         stop=lambda: self.closed or not self.resume.is_set())

    def session_key_figures(self, session_id, *, volume_m3=None, price_eur_per_kwh=None):
        """Key figures of one finished session with display values, else ``None``."""
        sessions = self.key_figures(volume_m3=volume_m3, price_eur_per_kwh=price_eur_per_kwh)
        index = next((i for i, entry in enumerate(sessions)
                      if entry["session_id"] == session_id), None)
        if index is None or sessions[index]["figures"] is None:
            return None
        # Vergleiche ohne Sitzungen im Testbetrieb (G25).
        earlier = [entry["figures"] for entry in sessions[:index]
                   if entry["figures"] is not None and not entry["test"]]
        return {
            "session_id": session_id,
            "revision": sessions[index]["revision"][0],
            "test": sessions[index]["test"],
            "test_source": sessions[index]["test_source"],
            "settings": {"volume_m3": volume_m3, "electricity_price_eur_per_kwh": price_eur_per_kwh},
            "figures": present_session(
                sessions[index]["figures"],
                earlier_warmup(earlier, program_kind(sessions[index]["figures"])),
            ),
        }

    def recognition_control(self, session_id, *, now=None):
        """Erkennungskontrolle einer Sitzung (B4b, Details · Erkennung), sonst ``None``.

        Liest nur gespeicherte Einträge; Sitzungen ohne Zustandsverlauf (vor B2)
        liefern Ereignisse ohne Spur und ohne verworfene Kandidaten.
        """
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("BEGIN")
            column = self._configuration_column(db)
            row = db.execute(
                "SELECT payload,ended_at" + column + " FROM sessions WHERE entry_id=? AND session_id=?",
                (self.entry_id, session_id),
            ).fetchone()
            if row is None:
                return None
            session = self._decode_payload(db, row[0], row[2] if column else None)

            def records(kind):
                return [json.loads(raw) for (raw,) in db.execute(
                    "SELECT payload FROM records WHERE entry_id=? AND session_id=? AND kind=? "
                    "ORDER BY id", (self.entry_id, session_id, kind),
                )]

            recorded = db.execute(
                "SELECT 1 FROM records WHERE entry_id=? AND session_id=? AND kind=? LIMIT 1",
                (self.entry_id, session_id, recognition_archive.STATE),
            ).fetchone() is not None
            return recognition_control(
                session,
                ended_at=row[1],
                now=now,
                detections=records("detection"),
                not_taken=records("detection_rejected"),
                states=records(recognition_archive.STATE) if recorded else None,
                rejected=records(recognition_archive.REJECTED_CANDIDATE) if recorded else None,
                episodes=records(recognition_archive.EPISODE),
            )

    def calibration_runs(self, run_id=None, *, active_run_id=None):
        """Kalibrierläufe aus dem Protokoll (B4e, Details · Kalibrierung).

        Auswahl und Ergebnis wie ``calibration_runs.view``; der laufende Lauf
        (``active_run_id``) steht im Zustand der Kalibrierung, nicht hier.
        """
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("BEGIN")
            column = self._configuration_column(db)

            def processed(session_id):
                row = db.execute(
                    "SELECT payload" + column + " FROM sessions WHERE entry_id=? AND session_id=?",
                    (self.entry_id, session_id),
                ).fetchone()
                if row is None:
                    return ()
                session = self._decode_payload(db, row[0], row[1] if column else None)
                return (session.get("timeline") or {}).get("processed") or ()

            return calibration_runs.view(
                [(session_id, json.loads(raw)) for session_id, raw in db.execute(
                    "SELECT session_id,payload FROM records WHERE entry_id=? "
                    "AND kind='calibration' ORDER BY id", (self.entry_id,),
                )],
                run_id=run_id, active_run_id=active_run_id, processed=processed,
            )

    def key_figure_statistics(self, zone, *, season=None, part=None, summer=None,
                              volume_m3=None, price_eur_per_kwh=None):
        """Statistics over the finished sessions of the whole archive, a season,
        one season part (autumn, winter, spring) or a summer.

        ``periods`` lists every season (with its parts) and summer with an
        evaluated session, so a caller can offer exactly the periods that have
        data. Session lists are grouped by season and summer for the whole
        archive and by season part for one season (``groups``).
        """
        period = (
            {"summer": summer} if summer is not None
            else {"season": season, **({"part": part} if part is not None else {})}
            if season is not None else {}
        )
        entries = self.key_figures(volume_m3=volume_m3, price_eur_per_kwh=price_eur_per_kwh)
        sessions = [entry for entry in entries if in_period(entry["started_at"], period)]
        evaluated = [{**entry["figures"], "test": entry["test"]}
                     for entry in sessions if entry["figures"] is not None]
        grouping = "season" if not period else "part" if set(period) == {"season"} else None
        return {
            "period": period,
            "periods": periods(
                [entry["started_at"] for entry in entries if entry["figures"] is not None]
            ),
            "session_ids": [figures["session_id"] for figures in evaluated],
            "test_session_ids": [figures["session_id"] for figures in evaluated
                                 if figures["test"]],
            "not_evaluated": [entry["session_id"] for entry in sessions
                              if entry["figures"] is None],
            "settings": {"volume_m3": volume_m3, "electricity_price_eur_per_kwh": price_eur_per_kwh},
            "figures": present_statistics(
                statistics_figures(evaluated, zone, volume_m3, grouping=grouping)
            ),
        }

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
            configuration_column = self._configuration_column(db)
            configuration_cache = OrderedDict()
            sessions = db.execute(
                "SELECT session_id,payload" + configuration_column + " FROM sessions "
                "WHERE entry_id=? AND ended_at IS NOT NULL ORDER BY ended_at,session_id",
                (self.entry_id,),
            ).fetchall()
            for row in sessions:
                session_id, payload = row[:2]
                try:
                    session = self._decode_payload(
                        db, payload, row[2] if configuration_column else None,
                        cache=configuration_cache,
                    )
                    configuration = session.get("configuration")
                    configuration = configuration if isinstance(configuration, Mapping) else {}
                    bindings = configuration.get("bindings", {})
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

    def detail_overview(self, session_id=None):
        """Details · Zustand (B4c): Umfang und Startdauer des Archivs, dazu die
        Temperaturmessungen der Sitzung ``session_id`` für K-Z2.

        Die Messungen werden je Sitzung zwischengespeichert und nur um neue Einträge
        ergänzt; ``points`` sind (Empfangszeit, Position, Wert).
        """
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("BEGIN")
            sessions = db.execute(
                "SELECT COUNT(*) FROM sessions WHERE entry_id=?", (self.entry_id,)
            ).fetchone()[0]
            records = db.execute(
                "SELECT COUNT(*) FROM records WHERE entry_id=?", (self.entry_id,)
            ).fetchone()[0]
            points = None
            if session_id is not None:
                cached = self._difference_points
                last, points = (
                    cached[1:] if cached is not None and cached[0] == session_id else (0, [])
                )
                rows = db.execute(
                    "SELECT id,received_at,json_extract(payload,'$.position'),"
                    "json_extract(payload,'$.value') FROM records WHERE entry_id=? "
                    "AND session_id=? AND id>? AND kind='measurement' "
                    "AND json_extract(payload,'$.quantity')='temperature' ORDER BY id",
                    (self.entry_id, session_id, last),
                ).fetchall()
                points = points + [(at, position, value) for _, at, position, value in rows]
                self._difference_points = (session_id, rows[-1][0] if rows else last, points)
        size = sum(
            path.stat().st_size
            for path in (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm"))
            if path.exists()
        )
        return {
            "sessions": sessions,
            "records": records,
            "size_bytes": size,
            "start_seconds": self.start_seconds,
            "points": points,
        }

    def export(self, chunk_bytes=None, *, check_only=False):
        """Prepare one streamed ZIP export; ``check_only`` stops at the fixed snapshot."""
        if chunk_bytes is None:
            chunk_bytes = self.parameters.values["archive_export_chunk_bytes"]
        return ArchiveExport(self, chunk_bytes, check_only)

    def _write_export(self, db, target):
        # One read transaction on the archive itself is the consistent state,
        # while new records keep being written. No copy, no temporary file.
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            configuration_column = self._configuration_column(db)
            configuration_cache = OrderedDict()
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
            # Unknown sizes go to data descriptors; ZIP64 keeps entries over 2 GiB valid.
            with archive.open("sessions.jsonl", "w", force_zip64=True) as out:
                for row in db.execute(
                    "SELECT session_id,payload" + configuration_column
                    + " FROM sessions WHERE entry_id=? ORDER BY started_at",
                    (self.entry_id,),
                ):
                    session = self._decode_payload(
                        db, row["payload"], row["configuration_id"] if configuration_column else None,
                        cache=configuration_cache,
                    )
                    delta = self._read_energy_delta(db, row["session_id"], session)
                    if delta is not None:
                        session["energy"]["intervals"] = delta["intervals"]
                    line = {"session_id": row["session_id"], **session}
                    out.write((encoded_plain(line) + "\n").encode())
            with archive.open("records.jsonl", "w", force_zip64=True) as out:
                for row in db.execute(
                    "SELECT * FROM records WHERE entry_id=? ORDER BY id",
                    (self.entry_id,),
                ):
                    data = self._decode_record(db, row, cache=configuration_cache)
                    out.write((encoded(data) + "\n").encode())
            with archive.open("measurements.csv", "w", force_zip64=True) as raw:
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
                        "sensor_id", "boot_id", "sample_id", "monotonic_ns",
                        "original_id",
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
                            *((data.get("sample") or {}).get(key) for key in (
                                "sensor_id", "boot_id", "sample_id", "monotonic_ns",
                            )),
                            data.get("original_id"),
                        )
                    )
                out.flush()
                out.detach()
