"""Linux regression: broad retained archive + six-hour original measurement stream.

Only rounded record counts inform this fixture; no private rows, timestamps,
source IDs, values, or session/gang details are copied. Timings are diagnostic;
SQLite VM work and equality of every returned record are the enforced contract.
"""

import json
import sqlite3
import tempfile
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from custom_components.ha_sauna import archive as archive_module
from custom_components.ha_sauna.archive import Archive
from test_archive_projection_reads import T0, session
from test_foundation import bindings
from archive_performance_fixture import KEYS, seed_volume, old_page_query


class ArchiveReadPerformanceTests(unittest.TestCase):
    def test_context_pages_do_not_scan_the_retained_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(Path(directory) / "volume.sqlite", "entry")
            archive._initialize(now=T0)
            end = T0 + timedelta(hours=6)
            now = end + timedelta(hours=1)
            stored = session("volume", ended_at=end, base_phases=[
                {"at": T0.isoformat(), "phase": "bereit", "operation_enabled": True}
            ])
            roles = bindings().as_dict()
            stored["configuration"] = {"bindings": {key: roles[key] for key in KEYS}}
            sources = seed_volume(archive, stored, T0)
            connect = sqlite3.connect
            loads = json.loads
            measurements = {}
            results = {}
            for mode in ("before", "after"):
                metrics = {"vm_steps": 0, "json_decodes": 0, "json_seconds": 0.0,
                           "response_bytes": 0, "pages": 0}

                class MeasuredConnection(sqlite3.Connection):
                    def execute(self, sql, parameters=(), /):
                        if mode == "before" and sql.startswith("SELECT * FROM records WHERE id IN ("):
                            sql, parameters = old_page_query(sql, parameters, sources, T0)
                        return super().execute(sql, parameters)

                def progress():
                    metrics["vm_steps"] += 1000
                    return 0

                def measured_connect(*args, **kwargs):
                    db = connect(*args, **kwargs, factory=MeasuredConnection)
                    db.set_progress_handler(progress, 1000)
                    return db

                def measured_loads(value, *args, **kwargs):
                    start = time.perf_counter()
                    result = loads(value, *args, **kwargs)
                    metrics["json_seconds"] += time.perf_counter() - start
                    metrics["json_decodes"] += 1
                    return result

                pages = []
                after = 0
                started = time.perf_counter()
                with (
                    patch.object(archive_module.sqlite3, "connect", measured_connect),
                    patch.object(archive_module.json, "loads", measured_loads),
                ):
                    while True:
                        page = archive.read("volume", after=after, limit=5000,
                                            kinds=("measurement",), now=now)
                        pages.append(page)
                        metrics["response_bytes"] += len(json.dumps(page).encode())
                        metrics["pages"] += 1
                        after = page["next_after"]
                        if after is None:
                            break
                metrics["seconds"] = time.perf_counter() - started
                measurements[mode] = metrics
                results[mode] = pages
            self.assertEqual(results["after"], results["before"])
            count = sum(len(page["records"]) for page in results["after"])
            self.assertEqual(count, 46_804)
            self.assertEqual(measurements["after"]["json_decodes"],
                             count + measurements["after"]["pages"])
            self.assertLess(measurements["after"]["vm_steps"],
                            measurements["before"]["vm_steps"] / 3,
                            json.dumps(measurements))
            print("ARCHIVE_VOLUME_BENCHMARK " + json.dumps({
                "background_records": 250_000, "selected_original_records": count,
                "session_hours": 6, "sources": 4, "measurement": measurements,
            }, sort_keys=True), flush=True)
