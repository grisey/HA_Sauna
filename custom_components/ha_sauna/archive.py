"""Originalauflösung und nachvollziehbare Zustandsrevisionen in privatem SQLite."""
from __future__ import annotations

import asyncio
from contextlib import closing
import csv
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import zipfile
from collections.abc import Mapping


def plain(value):
    if is_dataclass(value):
        return {field.name: plain(getattr(value, field.name)) for field in fields(value)}
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
    return json.dumps(plain(value), ensure_ascii=False, allow_nan=False, separators=(",", ":"))


class Archive:
    def __init__(self, path, entry_id):
        self.path = Path(path)
        self.entry_id = entry_id
        self.queue = asyncio.Queue()
        self.resume = asyncio.Event()
        self.resume.set()
        self.worker = None
        self.failure = None
        self.failed_records = []
        self.closed = False

    async def start(self):
        await asyncio.to_thread(self._initialize)
        self.worker = asyncio.create_task(self._run(), name=f"ha_sauna_archive_{self.entry_id}")

    def _initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript('''
                PRAGMA journal_mode=DELETE;
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT OR IGNORE INTO metadata VALUES ('schema', '1');
                CREATE TABLE IF NOT EXISTS records (
                    id INTEGER PRIMARY KEY, entry_id TEXT NOT NULL, session_id TEXT,
                    kind TEXT NOT NULL, received_at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS records_session ON records(entry_id, session_id, id);
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY, entry_id TEXT NOT NULL,
                    started_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    ended_at TEXT, payload TEXT NOT NULL);
            ''')
            if db.execute("SELECT value FROM metadata WHERE key='schema'").fetchone()[0] != '1':
                raise ValueError("Nicht unterstützte Archivversion")

    def append(self, kind, at, payload, session_id=None):
        if self.closed:
            raise RuntimeError("Archiv ist geschlossen")
        # Sofort kopieren: spätere Zustandsänderungen verändern keinen Auftrag.
        self.queue.put_nowait(("record", (kind, at.isoformat(), encoded(payload), session_id)))

    def save_session(self, session, at, configuration):
        payload = plain(session)
        payload["configuration"] = plain(configuration)
        self.append("session", at, payload, session.session_id)

    async def _run(self):
        while True:
            kind, payload = await self.queue.get()
            try:
                if kind == "stop":
                    return
                if kind == "fence":
                    if payload.cancelled():
                        continue
                    if self.failure:
                        payload.set_exception(self.failure)
                    else:
                        payload.set_result(None)
                elif kind == "pause":
                    self.resume.clear()
                    if self.failure:
                        payload.set_exception(self.failure)
                    else:
                        payload.set_result(None)
                    await self.resume.wait()
                else:
                    await asyncio.to_thread(self._write, payload)
            except Exception as error:
                self.failure = error
                if kind == "record":
                    self.failed_records.append(payload)
            finally:
                self.queue.task_done()

    def _write(self, record):
        kind, at, payload, session_id = record
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO records(entry_id,session_id,kind,received_at,payload) VALUES(?,?,?,?,?)",
                (self.entry_id, session_id, kind, at, payload))
            if kind == "session":
                data = json.loads(payload)
                db.execute('''INSERT INTO sessions VALUES(?,?,?,?,?,?)
                    ON CONFLICT(session_id) DO UPDATE SET updated_at=excluded.updated_at,
                    ended_at=excluded.ended_at,payload=excluded.payload''',
                    (session_id, self.entry_id, data["timeline"]["session_started_at"], at, data["ended_at"], payload))

    async def _barrier(self, kind):
        future = asyncio.get_running_loop().create_future()
        self.queue.put_nowait((kind, future))
        await future

    async def flush(self):
        await self._barrier("fence")

    async def pre_backup(self):
        # Alle bisherigen Aufträge sind dauerhaft geschrieben. Nur der Schreiber
        # pausiert; Regelung und Eingangserfassung dürfen weiterarbeiten.
        await self._barrier("pause")

    async def post_backup(self):
        self.resume.set()
        await self.flush()

    async def close(self):
        if self.closed:
            return
        self.resume.set()
        try:
            await self.flush()
        finally:
            self.closed = True
            self.queue.put_nowait(("stop", None))
            await self.worker

    def read(self, session_id=None, *, after=0, limit=1000):
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            if session_id is None:
                rows = db.execute("SELECT session_id,started_at,updated_at,ended_at FROM sessions WHERE entry_id=? ORDER BY started_at DESC", (self.entry_id,))
                return [dict(row) for row in rows]
            row = db.execute("SELECT payload FROM sessions WHERE entry_id=? AND session_id=?", (self.entry_id, session_id)).fetchone()
            if row is None:
                return None
            records = db.execute("SELECT * FROM records WHERE entry_id=? AND session_id=? AND id>? ORDER BY id LIMIT ?",
                (self.entry_id, session_id, after, limit)).fetchall()
            return {"session": json.loads(row["payload"]), "records": [
                {**dict(r), "payload": json.loads(r["payload"])} for r in records],
                "next_after": records[-1]["id"] if len(records) == limit else None}

    async def export(self):
        await self.flush()
        return await asyncio.to_thread(self._export)

    def _export(self):
        # SQLite-Backup liest einen konsistenten Stand, während neue Eingänge
        # weiter geschrieben werden. Die exportierte Datei ist niemals öffentlich.
        with tempfile.TemporaryDirectory(prefix="ha-sauna-export-") as directory:
            snapshot = Path(directory) / "snapshot.sqlite"
            with closing(sqlite3.connect(self.path)) as source, closing(sqlite3.connect(snapshot)) as target:
                source.backup(target)
            handle = tempfile.NamedTemporaryFile(prefix="ha-sauna-", suffix=".zip", delete=False)
            path = Path(handle.name)
            handle.close()
            try:
                with closing(sqlite3.connect(snapshot)) as db, zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
                    db.row_factory = sqlite3.Row
                    archive.writestr("manifest.json", encoded({"schema": 1, "entry_id": self.entry_id,
                        "resolution": "original_received", "timestamps": "ISO-8601 with timezone",
                        "records": "append-only; session records preserve prior assignments"}))
                    with archive.open("sessions.jsonl", "w") as out:
                        for row in db.execute("SELECT payload FROM sessions WHERE entry_id=? ORDER BY started_at", (self.entry_id,)):
                            out.write((row[0] + "\n").encode())
                    with archive.open("records.jsonl", "w") as out:
                        for row in db.execute("SELECT * FROM records WHERE entry_id=? ORDER BY id", (self.entry_id,)):
                            data = dict(row)
                            data["payload"] = json.loads(data["payload"])
                            out.write((encoded(data) + "\n").encode())
                    with archive.open("measurements.csv", "w") as raw:
                        out = io.TextIOWrapper(raw, encoding="utf-8", newline="")
                        writer = csv.writer(out)
                        writer.writerow(("record_id", "session_id", "received_at", "measured_at", "position", "quantity", "source", "value", "raw_value"))
                        for row in db.execute("SELECT * FROM records WHERE entry_id=? AND kind='measurement' ORDER BY id", (self.entry_id,)):
                            data = json.loads(row["payload"])
                            writer.writerow((row["id"], row["session_id"], data["received_at"], data["measured_at"],
                                data["position"], data["quantity"], data["source"], data["value"], data["raw_value"]))
                        out.flush()
                        out.detach()
                return path
            except BaseException:
                path.unlink(missing_ok=True)
                raise
