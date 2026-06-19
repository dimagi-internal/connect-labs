"""Pre-render ensure-and-sweep for the create-survey-solicitation walkthrough.

The walkthrough now films a **fully fresh lifecycle on camera**: scene 2 actually
clicks "Create Solicitation" (minting a brand-new ``type=solicitation`` record on
program 10008), scene 3 submits a response against it, scene 4 reads the response
id off the page, and scene 5 awards it — all threaded through the canopy
``capture`` action (``${solicitation_id}`` / ``${response_id}``). There is no fixed
canonical record to keep, and no per-render *reset* of a fixed award.

What this script does, per render, **before** the recorder starts:

1. **Ensure** the R6 — Attakar × Gura study group **4492** and its plan **4494**
   exist on program 10008 (the study-design demo seeds them). If either is
   missing, ERROR loudly — scenes 1-2 depend on them (the portfolio's "ready to
   solicit" group card and the create form's snapshotted coverage map).
2. **Sweep** EVERY ``type=solicitation`` record on program 10008 whose
   ``data.source_group_id == 4492`` (every call the walkthrough has ever minted),
   **and** all of their responses (``get_responses_for_solicitation`` → delete).
   Nothing is kept — each render mints its own fresh call, so the prior render's
   call + responses are cleared before the next one is minted. This keeps the
   program's solicitation set from accumulating a duplicate R6 call every render.

This replaces the two old per-render resets (``reset_solicitation.py`` swept
throwaways but KEPT a canonical #4495; ``reset_award.py`` rolled a fixed response
#4496 back to ``submitted``). Both are deleted — there are no fixed records left.

Why this goes over **ECS** (not the MCP, not local): records on program **10008**
are labs-only synthetic records (opp id >= 10_000). They live in the labs **prod**
DB and are reachable only *server-side, inside the labs app*, through the
local-records backend — ``SolicitationsDataAccess(program_id="10008")`` /
``ProgramPlanDataAccess(10008)``. The MCP solicitation tools hit the prod
``/export/labs_record/`` API and 404 for labs-only opps; a local
``manage.py shell`` would touch *your* dev DB, not labs prod. So we fire a one-off
``aws ecs run-task`` against the labs cluster — the same transport the old resets
used.

Requirements:
- AWS SSO session for the ``labs`` profile (``aws sso login --profile labs``).

Usage::

    python scripts/walkthroughs/create-survey-solicitation/ensure_demo.py

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
SOURCE_GROUP_ID = 4492  # R6 — Attakar × Gura study group scenes 1-2 solicit from
SOURCE_PLAN_ID = 4494  # the R6 plan snapshotted as the coverage area

# The server-side ensure-and-sweep, run inside the labs app:
#   (1) verify group 4492 + plan 4494 exist (ERROR if missing),
#   (2) delete every source_group_id==4492 solicitation AND all of its responses.
# Emits a single MARKER line this wrapper asserts the post-state from.
RESET_PY = f"""
from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess
from commcare_connect.solicitations.data_access import SolicitationsDataAccess

# (1) ensure the study-design demo seeds are present.
# access_token="dummy" satisfies the base data-access __init__ token check; the
# labs-only short-circuit (program 10008 >= 10_000) means no token is actually
# used — CRUD goes straight to the labs DB through the local-records backend.
pda = ProgramPlanDataAccess({PROGRAM_ID}, access_token="dummy")
group = pda.get_group({SOURCE_GROUP_ID})
plan = pda.get_plan({SOURCE_PLAN_ID})
if group is None or plan is None:
    print(
        "ENSURE_DEMO result=ERROR reason=seed_missing group_present=%s plan_present=%s "
        "hint=run_the_study-design_demo_seeder" % (group is not None, plan is not None)
    )
    raise SystemExit(1)

# (2) sweep every R6 call this walkthrough has minted + all of their responses
da = SolicitationsDataAccess(program_id="{PROGRAM_ID}", access_token="dummy")
sols = da.get_solicitations()

def _sgid(rec):
    v = rec.data.get("source_group_id")
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None

doomed_sols = [s for s in sols if _sgid(s) == {SOURCE_GROUP_ID}]
sol_ids = [s.id for s in doomed_sols]

response_ids = []
for s in doomed_sols:
    for r in da.get_responses_for_solicitation(s.id):
        response_ids.append(r.id)

# Delete responses first, then their solicitations.
if response_ids:
    da.labs_api.delete_records(response_ids)
if sol_ids:
    da.labs_api.delete_records(sol_ids)

# Re-read to confirm nothing source_group_id==4492 survives.
after = da.get_solicitations()
remaining_sols = [s.id for s in after if _sgid(s) == {SOURCE_GROUP_ID}]
remaining_responses = []
for s in after:
    if _sgid(s) == {SOURCE_GROUP_ID}:
        remaining_responses.extend(r.id for r in da.get_responses_for_solicitation(s.id))

ok = not remaining_sols and not remaining_responses
print(
    "ENSURE_DEMO result=%s group_present=True plan_present=True "
    "deleted_solicitations=%s deleted_responses=%s remaining_solicitations=%s "
    "remaining_responses=%s" % (
        "OK" if ok else "ERROR",
        sol_ids, response_ids, remaining_sols, remaining_responses,
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
        f"[ensure_demo] launching ECS ensure-and-sweep on program {PROGRAM_ID}: "
        f"verify group {SOURCE_GROUP_ID}/plan {SOURCE_PLAN_ID}, sweep all "
        f"source_group_id={SOURCE_GROUP_ID} solicitations + responses",
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
    print(f"[ensure_demo] task {task_id} running; waiting...", file=sys.stderr)
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

    # Pull logs and assert the marker. Retry briefly — CloudWatch can lag a couple
    # seconds behind task stop.
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
            if "ENSURE_DEMO result=" in line:
                marker = line.strip()
        if marker:
            break
        time.sleep(2)

    if not marker:
        print("[ensure_demo] FAILED: no ENSURE_DEMO marker in task logs", file=sys.stderr)
        return 1
    print(f"[ensure_demo] {marker}", file=sys.stderr)
    if "result=OK" not in marker:
        print(
            "[ensure_demo] FAILED: ensure-and-sweep did not complete cleanly "
            "(seed missing, or records survived the sweep)",
            file=sys.stderr,
        )
        return 1
    print(
        f"[ensure_demo] OK — group {SOURCE_GROUP_ID}/plan {SOURCE_PLAN_ID} present; "
        f"all source_group_id={SOURCE_GROUP_ID} solicitations + responses swept. "
        "Scene 2 mints a fresh call on camera.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
