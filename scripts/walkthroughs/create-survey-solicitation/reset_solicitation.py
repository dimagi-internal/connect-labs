"""Pre-render cleanup for the create-survey-solicitation walkthrough.

Deletes the **throwaway solicitations** that scene 2 of the walkthrough creates
when Maya actually clicks "Create Solicitation". Scene 2 now performs the real
publish (scroll + fill Description + click Create), which writes a brand-new
``type=solicitation`` LabsRecord on program **10008** every render. Without a
per-render sweep those throwaways accumulate forever, and the program's
solicitation list slowly fills with duplicates of the R6 — Attakar × Gura call.

What it deletes / keeps:
- DELETE every ``type=solicitation`` record on program 10008 whose
  ``data.source_group_id == 4492`` (the R6 — Attakar × Gura study group) —
  these are exactly the throwaways scene 2 mints — **EXCEPT** the canonical
  **#4495**.
- KEEP the canonical solicitation **#4495** (and, untouched, its response
  **#4496** — that lives under reset_award.py's care). Scenes 3/4/5 all run
  against #4495, so it must survive every sweep.

This deletes the *previous* render's throwaway BEFORE the current render mints a
new one, so at most one throwaway ever exists transiently, and #4495 stays the
single canonical call scenes 3-5 read.

Why this must run **per render**: scene 2 creates a fresh solicitation on every
render. Sweeping before each render keeps the program's solicitation set clean
(only #4495 + at most the one in-flight throwaway) and keeps scenes 3-5 honest
— they always read the canonical #4495, never a scene-2 leftover.

Why this goes over **ECS** (not the MCP, not local): solicitations on opp/program
**10008** are labs-only synthetic records (opp id >= 10_000). Their records live
in the labs **prod** DB and are reachable only *server-side, inside the labs app*,
through the local-records backend — ``SolicitationsDataAccess(program_id="10008")``.
The MCP solicitation tools hit the prod ``/export/labs_record/`` API and 404 for
labs-only opps; a local ``manage.py shell`` would touch *your* dev DB, not labs
prod. So we fire a one-off ``aws ecs run-task`` against the labs cluster — the
same transport reset_award.py uses.

Requirements:
- AWS SSO session for the ``labs`` profile (``aws sso login --profile labs``).

Usage::

    python scripts/walkthroughs/create-survey-solicitation/reset_solicitation.py

Env overrides: AWS_PROFILE_LABS, ECS_CLUSTER, ECS_TASK_DEF, ECS_SUBNET, ECS_SG.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

PROFILE = os.environ.get("AWS_PROFILE_LABS", "labs")
REGION = os.environ.get("AWS_REGION_LABS", "us-east-1")
CLUSTER = os.environ.get("ECS_CLUSTER", "labs-jj-cluster")
TASK_DEF = os.environ.get("ECS_TASK_DEF", "labs-jj-web")  # family -> latest revision
SUBNET = os.environ.get("ECS_SUBNET", "subnet-08a18eb47b48aff54")
SG = os.environ.get("ECS_SG", "sg-0666a5ed512c97d9d")
CONTAINER = os.environ.get("ECS_CONTAINER", "web")
LOG_GROUP = os.environ.get("ECS_LOG_GROUP", "/ecs/labs-jj-web")

PROGRAM_ID = "10008"
SOURCE_GROUP_ID = 4492  # R6 — Attakar × Gura study group scene 2 solicits from
CANONICAL_SOLICITATION_ID = 4495  # the one scenes 3/4/5 read — NEVER delete

# The server-side sweep. Lists every solicitation on program 10008, selects the
# throwaways scene 2 mints (source_group_id == 4492) EXCEPT the canonical #4495,
# and deletes them through the local records backend. Emits a MARKER line so this
# wrapper can assert the post-state from the logs.
RESET_PY = f"""
from commcare_connect.solicitations.data_access import SolicitationsDataAccess

da = SolicitationsDataAccess(program_id="{PROGRAM_ID}", access_token="dummy")
sols = da.get_solicitations()

# source_group_id may be stored as int or str depending on write path; compare loosely.
def _sgid(rec):
    v = rec.data.get("source_group_id")
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None

throwaways = [
    s for s in sols
    if _sgid(s) == {SOURCE_GROUP_ID} and s.id != {CANONICAL_SOLICITATION_ID}
]
deleted_ids = [s.id for s in throwaways]

if deleted_ids:
    da.labs_api.delete_records(deleted_ids)

# Re-read to confirm the canonical #4495 still exists and the throwaways are gone.
after = da.get_solicitations()
after_sgid = [s.id for s in after if _sgid(s) == {SOURCE_GROUP_ID}]
canonical_present = any(s.id == {CANONICAL_SOLICITATION_ID} for s in after)
remaining_throwaways = [i for i in after_sgid if i != {CANONICAL_SOLICITATION_ID}]

print(
    "RESET_SOLICITATION result=%s deleted=%s remaining_throwaways=%s canonical_present=%s" % (
        "OK" if (canonical_present and not remaining_throwaways) else "ERROR",
        deleted_ids, remaining_throwaways, canonical_present,
    )
)
"""


def _aws(*args: str) -> str:
    return subprocess.check_output(
        ["aws", *args, "--profile", PROFILE, "--region", REGION],
        text=True,
    ).strip()


def main() -> int:
    overrides = {
        "containerOverrides": [{"name": CONTAINER, "command": ["python", "manage.py", "shell", "-c", RESET_PY]}]
    }
    netcfg = f"awsvpcConfiguration={{subnets=[{SUBNET}]," f"securityGroups=[{SG}],assignPublicIp=ENABLED}}"
    print(
        f"[reset_solicitation] launching ECS sweep of scene-2 throwaway solicitations "
        f"(source_group_id={SOURCE_GROUP_ID}, keep #{CANONICAL_SOLICITATION_ID}) on program {PROGRAM_ID}",
        file=sys.stderr,
    )
    task_arn = _aws(
        "ecs",
        "run-task",
        "--cluster",
        CLUSTER,
        "--task-definition",
        TASK_DEF,
        "--launch-type",
        "FARGATE",
        "--network-configuration",
        netcfg,
        "--overrides",
        json.dumps(overrides),
        "--query",
        "tasks[0].taskArn",
        "--output",
        "text",
    )
    task_id = task_arn.rsplit("/", 1)[-1]
    print(f"[reset_solicitation] task {task_id} running; waiting...", file=sys.stderr)
    subprocess.run(
        [
            "aws",
            "ecs",
            "wait",
            "tasks-stopped",
            "--cluster",
            CLUSTER,
            "--tasks",
            task_arn,
            "--profile",
            PROFILE,
            "--region",
            REGION,
        ],
        check=True,
    )

    # Pull logs and assert the sweep marker. Retry briefly — CloudWatch can lag
    # a couple seconds behind task stop.
    stream = f"{CONTAINER}/{CONTAINER}/{task_id}"
    marker = ""
    for _ in range(6):
        try:
            msgs = _aws(
                "logs",
                "get-log-events",
                "--log-group-name",
                LOG_GROUP,
                "--log-stream-name",
                stream,
                "--query",
                "events[*].message",
                "--output",
                "text",
            )
        except subprocess.CalledProcessError:
            msgs = ""
        for line in msgs.splitlines():
            if "RESET_SOLICITATION result=" in line:
                marker = line.strip()
        if marker:
            break
        time.sleep(2)

    if not marker:
        print("[reset_solicitation] FAILED: no RESET_SOLICITATION marker in task logs", file=sys.stderr)
        return 1
    print(f"[reset_solicitation] {marker}", file=sys.stderr)
    if "result=OK" not in marker or "canonical_present=True" not in marker:
        print(
            "[reset_solicitation] FAILED: sweep did not leave #4495 as the only " "source_group_id=4492 solicitation",
            file=sys.stderr,
        )
        return 1
    print(
        f"[reset_solicitation] OK — throwaways swept; #{CANONICAL_SOLICITATION_ID} "
        "is the canonical call scenes 3-5 read",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
