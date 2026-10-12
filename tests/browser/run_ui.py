"""Bounded Linux UI checks with separate process and source evidence."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[2]


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def git(*arguments):
    return subprocess.check_output(
        ["git", *arguments], cwd=ROOT, timeout=30
    ).decode().strip()


def snapshot():
    files = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=ROOT, timeout=30
    ).decode().split("\0")
    return {
        "head": git("rev-parse", "HEAD"),
        "tree": git("rev-parse", "HEAD^{tree}"),
        "workflow_head": os.environ.get("GITHUB_SHA"),
        "status": git("status", "--porcelain"),
        "source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in sorted(files)
            if name and (ROOT / name).is_file()
        },
    }


class Evidence:
    def __init__(self, mode):
        runner_temp = os.environ.get("RUNNER_TEMP")
        runner_directory = (
            Path(runner_temp) / ("ha-sauna-ui-format" if mode == "format" else "ha-sauna-ui")
            if runner_temp else None
        )
        self.directory = Path(
            os.environ.get("UI_EVIDENCE") or runner_directory
            or tempfile.mkdtemp(prefix="ha-sauna-ui-")
        ).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.mode = mode
        self.path = self.directory / f"{mode}.json"
        self.data = {
            "mode": mode,
            "started_at": utc_now(),
            "completed": False,
            "source_before": snapshot(),
            "processes": [],
        }
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.data, indent=2, sort_keys=True) + "\n")

    def run(self, name, command, timeout=120, env=None, suite=None):
        if not 1 <= timeout <= 900:
            raise ValueError("A process timeout must be between 1 and 900 seconds")
        logfile = self.directory / f"{self.mode}-{name}.log"
        result = {
            "name": name, "command": command, "timeout_seconds": timeout,
            "started_at": utc_now(), "completed": False,
            "exit_code": None, "timed_out": False, "log": logfile.name,
        }
        self.data["processes"].append(result)
        self.save()
        start = time.monotonic()
        environment = os.environ.copy()
        environment.update(env or {})
        try:
            with logfile.open("w") as output:
                process = subprocess.Popen(
                    command, cwd=ROOT, env=environment, stdout=output,
                    stderr=subprocess.STDOUT, start_new_session=True,
                )
                try:
                    result["exit_code"] = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    result["timed_out"] = True
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=10)
                    result["exit_code"] = process.returncode
                result["completed"] = True
        except OSError as error:
            result["launch_error"] = str(error)
        result["ended_at"] = utc_now()
        result["elapsed_seconds"] = round(time.monotonic() - start, 3)
        result["passed"] = result["completed"] and result["exit_code"] == 0 and not result["timed_out"]
        if suite:
            output = logfile.read_text(errors="replace") if logfile.exists() else ""
            counts = re.findall(r"# tests (\d+)" if suite == "node" else r"Ran (\d+) tests?", output)
            skipped = re.findall(r"# skipped (\d+)|skipped=(\d+)", output)
            result["test_count"] = int(counts[-1]) if counts else 0
            result["skipped_count"] = sum(int(a or b) for a, b in skipped)
            result["passed"] &= result["test_count"] > 0 and result["skipped_count"] == 0
        self.save()
        print(json.dumps(result), flush=True)
        return result["passed"]

    def finish(self, error=None):
        self.data["completed"] = True
        self.data["ended_at"] = utc_now()
        self.data["source_after"] = snapshot()
        if error:
            self.data["error"] = str(error)
        before = self.data["source_before"]
        exact_head = not before["workflow_head"] or before["head"] == before["workflow_head"]
        passed = not error and exact_head and all(p["passed"] for p in self.data["processes"])
        if self.mode == "test":
            passed &= before["source_sha256"] == self.data["source_after"]["source_sha256"]
        self.data["passed"] = passed
        self.save()
        return 0 if passed else 1


def prepare(evidence):
    commands = [
        ("install-python", [sys.executable, "-m", "pip", "install", "-r", "tests/browser/requirements.txt"], 900),
        ("python-dependencies", [sys.executable, "-m", "pip", "check"], 120),
        ("compression-backend", [sys.executable, "-c", "from isal import isal_zlib; import aiohttp_fast_zlib; from aiohttp.compression_utils import ZLibBackend; assert aiohttp_fast_zlib.best_zlib is isal_zlib; aiohttp_fast_zlib.enable(); assert ZLibBackend.name == isal_zlib.__name__; print(ZLibBackend.name)"], 120),
        ("install-chromium", [sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"], 600),
    ]
    for name, command, timeout in commands:
        if not evidence.run(name, command, timeout):
            return
    from importlib.metadata import version

    # Keep dependencies outside the uploaded evidence directory.
    runtime = evidence.directory.parent / (evidence.directory.name + "-node")
    if evidence.run("install-node", ["npm", "install", "--prefix", str(runtime), "--ignore-scripts", "--no-package-lock", f"playwright@{version('playwright')}"], 300):
        environment_file = os.environ.get("GITHUB_ENV")
        if environment_file:
            with Path(environment_file).open("a") as output:
                output.write(f"NODE_PATH={runtime / 'node_modules'}\n")
    evidence.run("versions", [sys.executable, "-m", "pip", "freeze"], 120)
    evidence.run("node-version", ["node", "--version"])


def test(evidence):
    environment = {
        "HA_TEST_REQUIRED": "1", "ENGINE": "chromium",
        "HA_SAUNA_BROWSER_ARTIFACT_DIR": str(evidence.directory / "browser"),
        "HA_SAUNA_BROWSER_ARTIFACTS": str(evidence.directory / "browser"),
        "HISTORY_EVIDENCE": str(evidence.directory / "history"),
        "DETAILS_EVIDENCE": str(evidence.directory / "details"),
        "INTERACTION_EVIDENCE": str(evidence.directory / "history" / "interaction.json"),
    }
    panel_tests = sorted(str(path.relative_to(ROOT)) for path in (ROOT / "tests").glob("panel_*.test.js"))
    if not panel_tests:
        raise RuntimeError("No panel method tests found")
    evidence.run("panel", ["node", "--test", *panel_tests], 300, environment, "node")
    evidence.run("panel-api", [sys.executable, "-m", "unittest", "discover", "-s", "tests/integration", "-p", "test_panel*.py", "-v"], 300, environment, "unittest")
    for script in ("details", "accept", "profile", "interaction"):
        evidence.run(script, ["node", f"tests/browser/live_history/{script}.cjs"], 180, environment)
    evidence.run("ha-browser", [sys.executable, "-u", "tests/browser/run.py"], 900, environment, "unittest")


def format_changed(evidence):
    base = git("merge-base", "HEAD", os.environ.get("UI_BASE_REF", "origin/main"))
    changed = git("diff", "--name-only", "--diff-filter=ACMR", base, "HEAD", "--").splitlines()
    added = git("diff", "--name-only", "--diff-filter=A", base, "HEAD", "--").splitlines()
    javascript = [name for name in changed if Path(name).suffix in (".js", ".cjs")]
    python = [name for name in added if Path(name).suffix == ".py"]
    evidence.data["format_scope"] = {"base": base, "javascript": javascript, "new_python": python}
    evidence.save()
    if javascript:
        evidence.run("prettier", ["npx", "--yes", "prettier@3.6.2", "--write", *javascript], 600)
    if python:
        if evidence.run("install-ruff", [sys.executable, "-m", "pip", "install", "ruff"], 300):
            evidence.run("ruff-version", [sys.executable, "-m", "ruff", "--version"])
            evidence.run("ruff", [sys.executable, "-m", "ruff", "format", *python], 180)
    evidence.run("patch", ["git", "diff", "--binary", "--"], 120)
    shutil.copyfile(evidence.directory / "format-patch.log", evidence.directory / "format.patch")
    evidence.run("clean-diff", ["git", "diff", "--exit-code", "--"], 120)


def main():
    if sys.platform != "linux":
        raise SystemExit("UI checks and formatting run on Linux only")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "test", "format"))
    arguments = parser.parse_args()
    evidence = Evidence(arguments.mode)
    error = None
    try:
        {"prepare": prepare, "test": test, "format": format_changed}[arguments.mode](evidence)
    except Exception as exception:
        error = exception
        print(f"UI runner failed: {exception}", file=sys.stderr)
    return evidence.finish(error)


if __name__ == "__main__":
    raise SystemExit(main())
