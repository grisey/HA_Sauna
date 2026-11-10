"""N2: Sitzungsarchiv bereinigen (Nutzer 10.11.2026).

Aufruf:
    python tools/archive_cleanup.py QUELLE.sqlite ZIEL.sqlite [--trace PROTOKOLL.sqlite]

Die Quelle bleibt unverändert; sie wird über die SQLite-Sicherung kopiert, auch
im WAL-Modus vollständig. In der Kopie werden

- die Sitzungsstände (``session``) nach der Regel aus AL3 ausgedünnt: Ein Stand
  bleibt nur, wenn er sich inhaltlich vom zuletzt behaltenen Stand derselben
  Sitzung unterscheidet. Zähler (Heizzeit, Energie, Ofenkühlung) zählen nicht als
  Inhalt, wie ``SaunaRuntime.persist`` seit AL3. Der erste und der letzte Stand
  jeder Sitzung bleiben immer;
- die Einträge des Erkennungsprotokolls im Analyseprofil (``detector_trace``)
  entfernt, mit ``--trace`` vorher in eine eigene Datei gelegt;
- die Archivrevision erhöht, damit geöffnete Ansichten neu laden, und die Datei
  mit ``VACUUM`` verkleinert.

Sitzungen, Messwerte und alle übrigen Einträge bleiben unverändert. Ausgegeben
wird je Art die Zahl vorher und nachher.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

HEATING_COUNTERS = ("accounted_at", "elapsed_seconds")
AFTER_RUN_COUNTERS = ("remaining_seconds", "elapsed_seconds", "accounted_at")


def signature(payload: str, configuration_id) -> str:
    """Inhalt eines Sitzungsstands ohne Zähler, wie ``SaunaRuntime.persist`` (AL3)."""
    state = json.loads(payload)
    heating = state.get("heating")
    if isinstance(heating, dict):
        for counter in HEATING_COUNTERS:
            heating.pop(counter, None)
    state.pop("energy", None)
    after_run = state.get("after_run")
    if isinstance(after_run, dict):
        for counter in AFTER_RUN_COUNTERS:
            after_run.pop(counter, None)
    return json.dumps([state, configuration_id], sort_keys=True, separators=(",", ":"))


def redundant_states(db) -> list[int]:
    """IDs der Sitzungsstände, die inhaltlich ihrem Vorgänger gleichen."""
    drop = []
    sessions = [row[0] for row in db.execute(
        "SELECT DISTINCT session_id FROM records WHERE kind='session'")]
    for session_id in sessions:
        rows = db.execute(
            "SELECT id,payload,configuration_id FROM records WHERE kind='session' "
            "AND session_id IS ? ORDER BY id", (session_id,)).fetchall()
        kept = None
        for index, (identity, payload, configuration_id) in enumerate(rows):
            current = signature(payload, configuration_id)
            if index in (0, len(rows) - 1) or current != kept:
                kept = current
            else:
                drop.append(identity)
    return drop


def counts(db) -> dict:
    return dict(db.execute("SELECT kind,count(*) FROM records GROUP BY kind").fetchall())


def clean(source: Path, target: Path, trace: Path | None = None) -> dict:
    if target.exists():
        raise FileExistsError(target)
    with closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as src, \
            closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
    with closing(sqlite3.connect(target)) as db:
        before = counts(db)
        if trace is not None:
            if trace.exists():
                raise FileExistsError(trace)
            db.execute("ATTACH DATABASE ? AS trace", (str(trace),))
            db.execute("CREATE TABLE trace.records AS SELECT * FROM main.records "
                       "WHERE kind='detector_trace' ORDER BY id")
            db.commit()
            db.execute("DETACH DATABASE trace")
        drop = redundant_states(db)
        with db:
            db.executemany("DELETE FROM records WHERE id=?", ((i,) for i in drop))
            db.execute("DELETE FROM records WHERE kind='detector_trace'")
            row = db.execute("SELECT value FROM metadata WHERE key='revision'").fetchone()
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('revision', ?)",
                       (str(int(row[0]) + 1 if row else 1),))
        db.execute("VACUUM")
        after = counts(db)
        check = db.execute("PRAGMA integrity_check").fetchone()[0]
    if check != "ok":
        raise RuntimeError(f"Integritätsprüfung: {check}")
    return {"before": before, "after": after}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--trace", type=Path)
    args = parser.parse_args(argv)
    result = clean(args.source, args.target, args.trace)
    for kind in sorted(set(result["before"]) | set(result["after"])):
        before, after = result["before"].get(kind, 0), result["after"].get(kind, 0)
        print(f"{kind:28s} {before:9d} → {after:9d}")
    print(f"Größe {args.source.stat().st_size / 1e6:.1f} MB → {args.target.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
