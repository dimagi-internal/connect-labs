#!/usr/bin/env python3
"""Run a manage.py command on labs prod and read its output, sealed to you.

    .venv/bin/python tools/run_labs_command.py "backfill_flw_daily_summary_report --opp 123 --verify-only"

`run-labs-command.yml` no longer prints a command's output into the Actions log
(every repo reader can read that log for ~90 days). This makes a fresh X25519
keypair, dispatches the workflow with the public half as `recipient`, waits,
pulls the sealed envelope out of the run's log and opens it here. The private
key only ever lives in this process. Needs `gh` authenticated and the repo's
Python env (for `cryptography`).
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey  # noqa: E402

from connect_labs.utils.sealed_output import read_envelope, unseal  # noqa: E402

REPO = "dimagi-internal/connect-labs"
WORKFLOW = "run-labs-command.yml"


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 1
    sk = X25519PrivateKey.generate()
    pub = base64.b64encode(sk.public_key().public_bytes_raw()).decode()
    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    me = gh("api", "user", "--jq", ".login").strip()
    gh(
        "workflow",
        "run",
        WORKFLOW,
        "--repo",
        REPO,
        "--ref",
        "main",
        "-f",
        f"command={sys.argv[1]}",
        "-f",
        f"recipient={pub}",
    )
    run_id = None
    for _ in range(30):
        time.sleep(3)
        runs = json.loads(
            gh(
                "run",
                "list",
                "--repo",
                REPO,
                "--workflow",
                WORKFLOW,
                "--user",
                me,
                "--limit",
                "5",
                "--json",
                "databaseId,createdAt",
            )
        )
        fresh = [r for r in runs if r["createdAt"] >= since]
        if fresh:
            run_id = fresh[-1]["databaseId"]
            break
    if not run_id:
        print("dispatched, but could not find the run", file=sys.stderr)
        return 1
    print(f"run: https://github.com/{REPO}/actions/runs/{run_id}", file=sys.stderr)
    subprocess.run(["gh", "run", "watch", str(run_id), "--repo", REPO, "--exit-status"], capture_output=True)
    for _ in range(8):  # the log archive finalises a little after the run completes
        try:
            blob = read_envelope(gh("run", "view", str(run_id), "--repo", REPO, "--log"))
            break
        except Exception:  # noqa: BLE001
            time.sleep(10)
    else:
        print("no sealed output in the run log (did the task fail before run_sealed ran?)", file=sys.stderr)
        return 2
    sys.stdout.write(unseal(blob, sk).decode(errors="replace"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
