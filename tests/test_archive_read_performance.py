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
from archive_performance_fixture import (
    KEYS, ORIGINAL_RECORD_COUNT, seed_volume, old_page_query, seed_legacy_evidence,
)


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

            self.assert_legacy_cold_and_warm(archive, stored, now)

    def assert_legacy_cold_and_warm(self, archive, stored, now):
        _, counts = seed_legacy_evidence(archive, stored, T0)
        evidence_count = sum(counts[kind] for kind in ("phase", "source_state", "session"))
        loads = json.loads
        project = archive_module.project_archive
        connect = sqlite3.connect
        measurements, results = {}, {}
        relevant_count = counts["phase"] + counts["session"] + counts["heater_source_state"]
        for mode in ("before_cold", "before_warm", "after_cold", "after_warm"):
            before = mode.startswith("before_")
            cold = mode.endswith("_cold")
            if cold:
                archive._invalidate_projection("volume")
            metrics = {"json_decodes": 0, "json_seconds": 0.0,
                       "projection_calls": 0, "projection_seconds": 0.0,
                       "evidence_queries": 0, "pages": 0, "page_seconds": []}

            def measured_loads(value, *args, **kwargs):
                began = time.perf_counter()
                result = loads(value, *args, **kwargs)
                metrics["json_seconds"] += time.perf_counter() - began
                metrics["json_decodes"] += 1
                return result

            def measured_projection(*args, **kwargs):
                began = time.perf_counter()
                try:
                    return project(*args, **kwargs)
                finally:
                    metrics["projection_calls"] += 1
                    # Includes lazy SQLite evidence iteration and JSON loads.
                    metrics["projection_seconds"] += time.perf_counter() - began

            def traced(sql):
                if sql.startswith("SELECT kind,received_at,payload"):
                    metrics["evidence_queries"] += 1

            class MeasuredConnection(sqlite3.Connection):
                def execute(self, sql, parameters=(), /):
                    if before and sql.startswith("SELECT kind,received_at,payload"):
                        # Frozen old evidence SQL, identical database and all
                        # other production read/projection/cache behavior.
                        sql = (
                            "SELECT kind,received_at,payload FROM records WHERE entry_id=? "
                            "AND session_id=? AND kind IN ('phase','source_state','session') ORDER BY id"
                        )
                    return super().execute(sql, parameters)

            def measured_connect(*args, **kwargs):
                db = connect(*args, **kwargs, factory=MeasuredConnection)
                db.set_trace_callback(traced)
                return db

            pages, after = [], 0
            began = time.perf_counter()
            with (
                patch.object(archive_module.json, "loads", measured_loads),
                patch.object(archive_module, "project_archive", measured_projection),
                patch.object(archive_module.sqlite3, "connect", measured_connect),
            ):
                while True:
                    page_start = time.perf_counter()
                    page = archive.read("volume", after=after, limit=5000,
                                        kinds=("measurement",), now=now)
                    metrics["page_seconds"].append(time.perf_counter() - page_start)
                    pages.append(page)
                    metrics["pages"] += 1
                    after = page["next_after"]
                    if after is None:
                        break
            metrics["seconds"] = time.perf_counter() - began
            metrics["first_page_seconds"] = metrics["page_seconds"][0]
            measurements[mode], results[mode] = metrics, pages
            expected_evidence = (evidence_count if before else relevant_count) if cold else 0
            metrics["evidence_decodes"] = metrics["json_decodes"] - ORIGINAL_RECORD_COUNT - len(pages)
            self.assertEqual(metrics["projection_calls"], int(cold))
            self.assertEqual(metrics["evidence_queries"], int(cold))
            self.assertEqual(metrics["evidence_decodes"], expected_evidence)
        for mode, pages in results.items():
            self.assertEqual(pages, results["before_cold"], mode)
        self.assertEqual(sum(len(p["records"]) for p in results["after_cold"]), ORIGINAL_RECORD_COUNT)
        projection = results["after_cold"][0]["phase_projection"]
        self.assertIn("legacy_projection_incomplete", projection["corrections"])
        self.assertEqual(len({interval["source_id"] for interval in projection["intervals"]
                              if interval["phase"] == "saunagang"}), 12)
        self.assertTrue(projection["readiness_pauses"])
        print("ARCHIVE_LEGACY_VOLUME_BENCHMARK " + json.dumps({
            "original_measurement_records": ORIGINAL_RECORD_COUNT, "session_hours": 6,
            "gangs": 12, "evidence_counts": counts,
            "all_evidence_records": evidence_count,
            "semantically_relevant_evidence_records": relevant_count,
            "measurement": measurements,
            "scope": "same database, old/current evidence SQL, each cold then warm; identical full responses",
        }, sort_keys=True), flush=True)
