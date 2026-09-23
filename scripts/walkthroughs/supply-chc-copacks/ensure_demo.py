"""Setup entrypoint for the `supply-chc-copacks` walkthrough.

The recipe's ``setup:`` command. The narrative is state-mutating -- the lead
places the order and pays, the distributor confirms, receives and releases
through its update link, the programme subscribes to an alert and records a
stock take -- so every render must start from the same world. This reseeds it.

It ships ``seed_remote.py`` to the deployed labs worker over ECS exec and runs
it through ``manage.py shell`` (see that file for why not the MCP), then writes
the ids and the update-link path the recipe's ``${var}``s resolve from to
``.realized.json`` beside this file. That file is gitignored: it carries the
link's raw token, which must never be committed.

Needs an AWS session for the labs account (``AWS_PROFILE=labs``).

    python scripts/walkthroughs/supply-chc-copacks/ensure_demo.py
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTPUTS = HERE / ".realized.json"
CLUSTER = "labs-jj-cluster"
SERVICE = "labs-jj-worker"
CONTAINER = "worker"
REGION = "us-east-1"
MARK = "SUPPLY_CHC_COPACKS_RESULT"


def _aws(*args: str, stdin=None, timeout=600) -> subprocess.CompletedProcess:
    env = {**os.environ, "AWS_PROFILE": os.environ.get("AWS_PROFILE", "labs"), "AWS_PAGER": ""}
    return subprocess.run(
        ["aws", *args, "--region", REGION],
        env=env,
        stdin=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _worker_task() -> str:
    result = _aws(
        "ecs",
        "list-tasks",
        "--cluster",
        CLUSTER,
        "--service-name",
        SERVICE,
        "--desired-status",
        "RUNNING",
        "--query",
        "taskArns",
        "--output",
        "text",
    )
    if result.returncode != 0:
        sys.exit(f"could not list labs worker tasks (is the labs AWS session live?): {result.stderr.strip()}")
    tasks = result.stdout.split()
    if not tasks:
        sys.exit("no running labs worker task")
    return tasks[-1]


def main() -> None:
    source = (HERE / "seed_remote.py").read_text()
    encoded = base64.b64encode(source.encode()).decode()
    command = f"python manage.py shell -c \"exec(__import__('base64').b64decode('{encoded}').decode())\""
    print("seeding programme 10601 on the labs worker…", flush=True)
    # The interactive session needs a live stdin until the command finishes;
    # a sleeping subprocess holds it open.
    holder = subprocess.Popen(["sleep", "240"], stdout=subprocess.PIPE)
    try:
        result = _aws(
            "ecs",
            "execute-command",
            "--cluster",
            CLUSTER,
            "--task",
            _worker_task(),
            "--container",
            CONTAINER,
            "--interactive",
            "--command",
            command,
            stdin=holder.stdout,
        )
    finally:
        holder.kill()
    output = result.stdout + result.stderr
    match = re.search(MARK + r"(\{.*?\})" + MARK, output, re.S)
    if not match:
        tail = "\n".join(output.splitlines()[-40:])
        sys.exit(f"seed did not report a result (exit {result.returncode}):\n{tail}")
    realized = json.loads(match.group(1).replace("\r", "").replace("\n", ""))
    OUTPUTS.write_text(json.dumps({k: v for k, v in realized.items() if k != "purged"}, indent=2) + "\n")
    print(f"seeded: round {realized['round_id']}, order {realized['copack_order_id']}; purged {realized['purged']}")
    print(f"outputs → {OUTPUTS}")


if __name__ == "__main__":
    main()
