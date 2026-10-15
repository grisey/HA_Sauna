"""Profile the unchanged session unittest discovery with a few timed boundaries.

Linux invocation from the repository root::

    SAUNA_SESSION_EVIDENCE=/tmp/sessions python tests/sessions/profile_replay.py

SAUNA_SESSION_CASE and SAUNA_SESSION_EVIDENCE retain their normal meaning.
The additional report defaults to EVIDENCE/replay-profile.json, or
./replay-profile.json without EVIDENCE; --output selects another report path.
No timing assertion or alternative replay/test implementation is introduced.
"""

from __future__ import annotations

import argparse
import functools
import inspect
import json
import os
import platform
import resource
import sys
import threading
import time
import unittest
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path


SESSION_ROOT = Path(__file__).resolve().parent
DISCOVERY = ["unittest", "discover", "-s", "tests/sessions", "-v"]


class SessionProfile:
    """Aggregate only named boundaries, separately for each worker thread."""

    def __init__(self):
        self.case = ContextVar("profile_case", default=None)
        self.route = ContextVar("profile_route", default=None)
        self.fit_mode = ContextVar("profile_fit_mode", default=None)
        self.direction = ContextVar("profile_direction", default=None)
        self.record_kind = ContextVar("profile_record_kind", default=None)
        self.query = ContextVar("profile_query", default=None)
        self.witness = ContextVar("profile_witness", default=False)
        self.local = threading.local()
        self.threads = []
        self.thread_lock = threading.Lock()
        self.patches = []
        self.instrumented = []
        self.unavailable = []
        self.instrumentation_error = None
        self.result = None

    def _stats(self):
        if not hasattr(self.local, "stats"):
            self.local.stats = {}
            self.local.queries = {}
            with self.thread_lock:
                self.threads.append({
                    "name": threading.current_thread().name,
                    "ident": threading.get_ident(),
                    "metrics": self.local.stats,
                    "queries": self.local.queries,
                })
        return self.local.stats

    def _start(self, name):
        key = (self.case.get(), name, self.fit_mode.get(), self.route.get(),
               self.direction.get(), self.record_kind.get())
        stats = self._stats().setdefault(key, [0, 0, 0, 0])
        return stats, time.perf_counter_ns(), time.thread_time_ns()

    @staticmethod
    def _finish(start, failed):
        stats, wall, cpu = start
        cpu = time.thread_time_ns() - cpu
        wall = time.perf_counter_ns() - wall
        stats[0] += 1
        stats[1] += int(failed)
        stats[2] += wall
        stats[3] += cpu

    def timed(self, original, name):
        if inspect.iscoroutinefunction(original):
            @functools.wraps(original)
            async def asynchronous(*args, **kwargs):
                start = self._start(name)
                failed = True
                try:
                    result = await original(*args, **kwargs)
                    failed = False
                    return result
                finally:
                    self._finish(start, failed)

            return asynchronous

        @functools.wraps(original)
        def synchronous(*args, **kwargs):
            start = self._start(name)
            failed = True
            try:
                result = original(*args, **kwargs)
                failed = False
                return result
            finally:
                self._finish(start, failed)

        return synchronous

    def patch(self, owner, attribute, replacement):
        original = getattr(owner, attribute)
        self.patches.append((owner, attribute, original))
        setattr(owner, attribute, replacement)

    def boundary(self, owner, attribute, name):
        if owner is None or not hasattr(owner, attribute):
            self.unavailable.append(name)
            return
        self.patch(owner, attribute, self.timed(getattr(owner, attribute), name))
        self.instrumented.append(name)

    def _curve_query(self, original):
        measured = self.timed(original, "detector.curve_query")

        @functools.wraps(original)
        def query(detector, route, position, quantity, *args, **kwargs):
            key = (route, position, quantity)
            previous = detector._curve_evaluated.get(key)
            observation = {"fits": 0}
            route_token = self.route.set(route)
            query_token = self.query.set(observation)
            failed = True
            try:
                result = measured(detector, route, position, quantity, *args, **kwargs)
                failed = False
                return result
            finally:
                self._stats()
                counts = self.local.queries.setdefault(
                    (self.case.get(), route, position.value, quantity.value),
                    {"queries": 0, "regular_fits": 0, "failed_queries": 0,
                     "without_fit_unchanged_evaluation": 0,
                     "without_fit_changed_evaluation": 0},
                )
                counts["queries"] += 1
                counts["regular_fits"] += observation["fits"]
                if failed:
                    counts["failed_queries"] += 1
                elif not observation["fits"]:
                    unchanged = previous is detector._curve_evaluated.get(key)
                    suffix = "unchanged" if unchanged else "changed"
                    counts[f"without_fit_{suffix}_evaluation"] += 1
                self.query.reset(query_token)
                self.route.reset(route_token)

        return query

    def _curve_call(self, original):
        measured = self.timed(original, "curve_change")

        @functools.wraps(original)
        def curve(*args, **kwargs):
            if self.witness.get():
                mode = "witness_correction"
            else:
                mode = "free_search" if kwargs.get("onset_at") is None else "fixed_onset"
                observation = self.query.get()
                if observation is not None:
                    observation["fits"] += 1
            token = self.fit_mode.set(mode)
            direction_token = self.direction.set(kwargs.get("direction"))
            try:
                return measured(*args, **kwargs)
            finally:
                self.direction.reset(direction_token)
                self.fit_mode.reset(token)

        return curve

    def _witness_correction(self, original):
        measured = self.timed(original, "detector.correct_curve_proposals")

        @functools.wraps(original)
        def correct(*args, **kwargs):
            token = self.witness.set(True)
            try:
                return measured(*args, **kwargs)
            finally:
                self.witness.reset(token)

        return correct

    def _archive_record(self, original, name, tuple_record):
        measured = self.timed(original, name)

        @functools.wraps(original)
        def record(archive, item, *args, **kwargs):
            token = self.record_kind.set(item[0] if tuple_record else item)
            try:
                return measured(archive, item, *args, **kwargs)
            finally:
                self.record_kind.reset(token)

        return record

    def install(self):
        # Discovery imports the original test/runner/product modules first.
        # Missing imports stay normal unittest import errors, not profiler imports.
        runner = sys.modules.get("runner")
        prefix = "custom_components.ha_sauna."
        detector = sys.modules.get(prefix + "core.detector")
        curve = sys.modules.get(prefix + "core.curve_change")
        archive = sys.modules.get(prefix + "archive")
        runtime = sys.modules.get(prefix + "runtime")
        device = sys.modules.get(prefix + "device")
        session_class = getattr(runner, "SessionRun", None)
        detector_class = getattr(detector, "Detector", None)
        archive_class = getattr(archive, "Archive", None)
        runtime_class = getattr(runtime, "SaunaRuntime", None)

        if session_class is not None:
            original_init = session_class.__init__

            @functools.wraps(original_init)
            def initialize(session, case):
                # Each test case runs sequentially; to_thread copies this context.
                self.case.set(case["id"])
                return original_init(session, case)

            self.patch(session_class, "__init__", initialize)
        for attribute in ("run", "evaluate", "snapshot", "_export_archive"):
            self.boundary(session_class, attribute, f"session.{attribute}")
        for attribute in ("persist", "_run_cycle"):
            self.boundary(runtime_class, attribute, f"runtime.{attribute}")

        if detector_class is not None:
            self.patch(detector_class, "_curve_candidate",
                       self._curve_query(detector_class._curve_candidate))
            self.patch(detector_class, "_correct_curve_proposals",
                       self._witness_correction(detector_class._correct_curve_proposals))
            self.instrumented.extend(("detector.curve_query",
                                      "detector.correct_curve_proposals"))
        if detector is not None and curve is not None:
            wrapper = self._curve_call(curve.curve_change)
            self.patch(curve, "curve_change", wrapper)
            self.patch(detector, "curve_change", wrapper)
            self.instrumented.append("curve_change")
        for attribute in ("_models", "_fit", "_least_squares"):
            self.boundary(curve, attribute, f"curve.{attribute}")

        for attribute in ("append", "save_session", "read", "flush",
                          "export", "_export"):
            self.boundary(archive_class, attribute, f"archive.{attribute}")
        if archive_class is not None:
            for attribute in ("_append_plain", "_write"):
                name = f"archive.{attribute}"
                self.patch(archive_class, attribute, self._archive_record(
                    getattr(archive_class, attribute), name, attribute == "_write",
                ))
                self.instrumented.append(name)
        # Keep archive.plain and all its recursive calls completely untouched.
        # Archive-internal conversion is included in append/save_session/export.
        for module, name in ((runner, "runner"), (runtime, "runtime"), (device, "device")):
            self.boundary(module, "plain", f"{name}.plain")
        for module, name in ((archive, "archive"), (runtime, "runtime")):
            self.boundary(module, "encoded_plain", f"{name}.encoded_plain")

    def restore(self):
        for owner, attribute, original in reversed(self.patches):
            setattr(owner, attribute, original)

    def report(self):
        metrics, queries = [], []
        for thread in self.threads:
            identity = {"thread_name": thread["name"], "thread_ident": thread["ident"]}
            for key, (calls, errors, wall, cpu) in thread["metrics"].items():
                case, name, mode, route, direction, record_kind = key
                metrics.append({
                    **identity, "case": case, "name": name, "fit_mode": mode,
                    "route": route, "direction": direction,
                    "record_kind": record_kind,
                    "calls": calls, "errors": errors,
                    "wall_ns": wall, "thread_cpu_ns": cpu,
                })
            for (case, route, position, quantity), counts in thread["queries"].items():
                queries.append({**identity, "case": case, "route": route,
                                "position": position, "quantity": quantity, **counts})
        result = self.result
        return {
            "instrumented": sorted(self.instrumented),
            "unavailable": sorted(self.unavailable),
            "instrumentation_error": self.instrumentation_error,
            "metrics": sorted(metrics, key=lambda row: (
                row["case"] or "", row["name"], row["fit_mode"] or "",
                row["route"] or "", row["direction"] or 0,
                row["record_kind"] or "", row["thread_ident"],
            )),
            "curve_queries": sorted(queries, key=lambda row: (
                row["case"] or "", row["route"], row["position"],
                row["quantity"], row["thread_ident"],
            )),
            "unittest": None if result is None else {
                "tests_run": result.testsRun,
                "successful": result.wasSuccessful(),
                "failures": len(result.failures), "errors": len(result.errors),
                "skipped": [{"test": str(test), "reason": reason}
                            for test, reason in result.skipped],
                "expected_failures": len(result.expectedFailures),
                "unexpected_successes": len(result.unexpectedSuccesses),
            },
        }


def metadata():
    names = ("RUNNER_NAME", "RUNNER_OS", "RUNNER_ARCH", "ImageOS", "ImageVersion",
             "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB", "GITHUB_SHA",
             "GITHUB_REF", "HA_TEST_REQUIRED", "SAUNA_SESSION_CASE",
             "SAUNA_SESSION_EVIDENCE", "TMPDIR")
    return {
        "python": sys.version, "executable": sys.executable,
        "implementation": platform.python_implementation(),
        "platform": platform.platform(), "machine": platform.machine(),
        "processor": platform.processor(), "cpu_count": os.cpu_count(),
        "cpu_affinity": sorted(os.sched_getaffinity(0))
        if hasattr(os, "sched_getaffinity") else None,
        "environment": {name: os.environ[name] for name in names if name in os.environ},
    }


def usage():
    values = resource.getrusage(resource.RUSAGE_SELF)
    return {name: getattr(values, name) for name in (
        "ru_utime", "ru_stime", "ru_maxrss", "ru_minflt", "ru_majflt",
        "ru_nvcsw", "ru_nivcsw",
    )}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    output = arguments.output or (
        Path(os.environ.get("SAUNA_SESSION_EVIDENCE", ".")) / "replay-profile.json"
    )
    profile = SessionProfile()

    class ProfileRunner(unittest.TextTestRunner):
        def run(self, suite):
            try:
                try:
                    profile.install()
                except Exception as error:
                    profile.instrumentation_error = f"{type(error).__name__}: {error}"
                    profile.restore()
                    profile.patches.clear()
                    profile.instrumented.clear()
                    print(f"Session profile instrumentation failed: {error}",
                          file=sys.stderr, flush=True)
                profile.result = super().run(suite)
                return profile.result
            finally:
                profile.restore()

    started = datetime.now(UTC).isoformat()
    machine = metadata()
    before = usage()
    wall, cpu = time.perf_counter_ns(), time.process_time_ns()
    exit_code, exception = None, None
    try:
        # Match python -m unittest's repository-root import priority.
        sys.path[0] = str(SESSION_ROOT.parents[1])
        unittest.main(module=None, argv=DISCOVERY, testRunner=ProfileRunner)
    except SystemExit as error:
        exit_code = error.code if isinstance(error.code, int) else int(error.code is not None)
        raise
    except BaseException as error:
        exit_code = 130 if isinstance(error, KeyboardInterrupt) else 1
        exception = type(error).__name__
        raise
    finally:
        total_cpu = time.process_time_ns() - cpu
        total_wall = time.perf_counter_ns() - wall
        try:
            report = {
                "schema": 1, "started_at": started,
                "finished_at": datetime.now(UTC).isoformat(),
                "metadata": machine, "discovery_argv": DISCOVERY,
                "exit_code": exit_code, "exception": exception,
                "total": {"wall_ns": total_wall, "process_cpu_ns": total_cpu},
                "resource_before": before, "resource_after": usage(),
                "measurement_contract": [
                    "Wall and thread CPU spans are inclusive and overlap; do not sum them.",
                    "Async spans include suspension and other work on the same thread.",
                    "Total process CPU includes executor threads; per-thread spans do not.",
                    "Named wrappers add overhead; these are profiled, not baseline timings.",
                    "Total spans discovery and test execution, excluding metadata/report output.",
                    "No global call tracing; archive.plain recursion stays untouched.",
                    "Imported plain aliases only; archive conversion is inside archive spans.",
                    "Queries without fits include caches and early returns, not cache hits alone.",
                    "Changed evaluations without fits can include expired-candidate returns.",
                    "ZIP compression is included in archive._export, not timed separately.",
                    "Unwrapped test evidence JSON/gzip writing is included in total only.",
                    "Skipped tests retain normal unittest status and are listed explicitly.",
                ],
                **profile.report(),
            }
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
            print(f"Session profile: {output}", file=sys.stderr, flush=True)
        except Exception as error:
            # Report failures must never replace the original unittest exit/error.
            print(f"Session profile report failed: {type(error).__name__}: {error}",
                  file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
