"""Pre-render setup for the create-survey-solicitation walkthrough.

Resets solicitation response **#4496** (Health Bridge Nigeria / Amina Okafor,
coverage plan 4494) back to ``status: submitted`` and clears the award fields
(``reward_budget``, ``awarded_at``, ``awardee_notified``, ``org_id``) so the
award page (``/solicitations/response/4496/award/``) renders the **award form**
(budget input + "Award" button) again — not the post-award confirmation.

Why this must run **per render**: scene 5 of the walkthrough actually clicks
"Award", which re-awards #4496. If the next render starts from the awarded
end-state, scene 5's ``wait_for input[name=reward_budget]`` / ``fill`` / ``click``
time out (the form isn't rendered) and scene 4 also shows an already-awarded
response *before* Maya awards it. Resetting before every render keeps the
recorded flow temporally honest and lets scene 5's actions succeed.

Why this goes over **ECS** (not the MCP, not local): #4496 lives on opp/program
**10008**, a labs-only synthetic opp (id >= 10_000). Its records live in the labs
**prod** DB and are reachable only *server-side, inside the labs app*, through the
local-records backend — ``SolicitationsDataAccess(program_id="10008")``. The MCP
solicitation tools hit the prod ``/export/labs_record/`` API and 404 for labs-only
opps; a local ``manage.py shell`` would touch *your* dev DB, not labs prod. So we
fire a one-off ``aws ecs run-task`` against the labs cluster — the same transport
the plan-transition reset used.

NOTE: the labs backend's ``update_record`` does a FULL data replace (not a merge),
so the server-side reset re-sends the COMPLETE response data dict with only the
award fields changed; everything else (selected_plan_ids=[4494], org/persona
identity, submission date) is preserved verbatim.

Requirements:
- AWS SSO session for the ``labs`` profile (``aws sso login --profile labs``).

Usage::

    python scripts/walkthroughs/create-survey-solicitation/reset_award.py

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

RESPONSE_ID = 4496
PROGRAM_ID = "10008"

# The server-side reset. Reads #4496, rewrites the COMPLETE data dict with the
# award fields cleared, and writes it back through the local records backend.
# Emits MARKER lines so this wrapper can assert the post-state from the logs.
RESET_PY = f"""
import json
from commcare_connect.solicitations.data_access import SolicitationsDataAccess

da = SolicitationsDataAccess(program_id="{PROGRAM_ID}", access_token="dummy")
r = da.get_response_by_id({RESPONSE_ID})
if not r:
    print("RESET_AWARD result=ERROR reason=response_not_found")
    raise SystemExit(1)

data = dict(r.data)
# Reset to the pre-award submitted state. update_record FULL-REPLACES data, so we
# send the whole dict; only the award markers change, everything else is preserved.
data["status"] = "submitted"
data["reward_budget"] = None
data["awarded_at"] = None
data["awardee_notified"] = False
data["org_id"] = ""
da.update_response({RESPONSE_ID}, data)

after = da.get_response_by_id({RESPONSE_ID})
print(
    "RESET_AWARD result=OK status=%s reward_budget=%r awarded_at=%r "
    "awardee_notified=%r org_id=%r plans=%s org=%s" % (
        after.status, after.data.get("reward_budget"), after.data.get("awarded_at"),
        after.data.get("awardee_notified"), after.data.get("org_id"),
        after.data.get("selected_plan_ids"), after.data.get("org_name"),
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
    print(f"[reset_award] launching ECS reset of response #{RESPONSE_ID} on program {PROGRAM_ID}", file=sys.stderr)
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
    print(f"[reset_award] task {task_id} running; waiting...", file=sys.stderr)
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

    # Pull logs and assert the reset marker. Retry briefly — CloudWatch can lag
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
            if "RESET_AWARD result=" in line:
                marker = line.strip()
        if marker:
            break
        time.sleep(2)

    if not marker:
        print("[reset_award] FAILED: no RESET_AWARD marker in task logs", file=sys.stderr)
        return 1
    print(f"[reset_award] {marker}", file=sys.stderr)
    if "result=OK" not in marker or "status=submitted" not in marker:
        print("[reset_award] FAILED: reset did not land status=submitted", file=sys.stderr)
        return 1
    print("[reset_award] OK — #4496 is submitted; award form will render", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
