"""Record one GitHub Actions test process without changing its command or result."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
                    encoding="utf-8")


def main():
    folder = Path(sys.argv[1])
    command = sys.argv[2:]
    if not command:
        raise ValueError("Expected evidence directory and process command")
    folder.mkdir(parents=True, exist_ok=True)

    def git(*args):
        return subprocess.check_output(["git", *args], text=True).strip()

    checkout, checkout_tree = git("rev-parse", "HEAD"), git("rev-parse", "HEAD^{tree}")
    pr_head = os.environ.get("SAUNA_PR_HEAD_SHA") or None
    if not pr_head and os.environ.get("GITHUB_EVENT_NAME") == "pull_request":
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        pr_head = event["pull_request"]["head"]["sha"]
    pr_tree = git("rev-parse", f"{pr_head}^{{tree}}") if pr_head else None
    (folder / "commit.txt").write_text(checkout + "\n", encoding="utf-8")
    (folder / "tree.txt").write_text(checkout_tree + "\n", encoding="utf-8")
    write_json(folder / "run-metadata.json", {
        "schema_version": 1,
        "pr_head": {"commit": pr_head, "tree": pr_tree} if pr_head else None,
        "checkout": {
            "commit": checkout, "tree": checkout_tree,
            "symbolic_branch": git("branch", "--show-current") or None,
            "github_ref": os.environ["GITHUB_REF"],
            "head_ref": os.environ.get("GITHUB_HEAD_REF") or None,
        },
        "tree_comparison": ("equal" if pr_tree == checkout_tree else "different")
                           if pr_tree else "no_pr_head",
        "workflow": os.environ["GITHUB_WORKFLOW"],
        "run_id": os.environ["GITHUB_RUN_ID"],
        "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
        "job": os.environ["GITHUB_JOB"],
        "case": os.environ.get("SAUNA_SESSION_CASE"),
        "runner_os": os.environ["RUNNER_OS"],
        "process": {
            "command": shlex.join(command), "argv": command,
            "log": "process.log", "exit_code": "exit-code.txt",
            "completion": "process-completion.json",
        },
    })
    with (folder / "process.log").open("w", encoding="utf-8") as log:
        process = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False)
    result = process.returncode
    (folder / "exit-code.txt").write_text(str(result) + "\n", encoding="utf-8")
    write_json(folder / "process-completion.json", {
        "process_returned": True,
        "exit_code": result,
        "job_conclusion": "Recorded separately by GitHub Actions after artifact upload",
    })
    with (folder / "process.log").open(encoding="utf-8", errors="replace") as log:
        for line in log:
            print(line, end="")
    return result if result >= 0 else 128 - result


if __name__ == "__main__":
    sys.exit(main())
