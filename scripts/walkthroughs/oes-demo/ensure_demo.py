"""Seed the OES demo environment on the deployed labs.

Ships ``seed_remote.py`` to the labs worker over ECS exec and runs it through
``manage.py shell`` -- the same route every supply walkthrough seeder uses, and
for the same reason: every write tool is rate-limited per user and this seed is
several hundred writes.

It then writes the program ids, the seeded order ids and the partner link URLs
to ``outputs.json`` beside this file. **That file is gitignored**: the URLs
carry the links' raw tokens, and this repository is public.

    AWS_PROFILE=labs python scripts/walkthroughs/oes-demo/ensure_demo.py \
        --drive-folder <folder-id>

Needs a live AWS session for the labs account -- check it with
``aws sts get-caller-identity --profile labs`` before starting, because a seed
that dies halfway leaves a partial environment and there is no wholesale undo
(see "Running it twice" below).

## The payload carries its own Drive loader

``load_seed_data`` lives in ``connect_labs/labs/synthetic/seed_data.py``, which
exists on this branch and **not on the deployed labs** -- a seeder that
imported it died on the worker with ``ModuleNotFoundError`` even though it
passed locally. So that module's source travels with the payload and is exec'd
into its own namespace, exactly as ``seed_remote.py`` already was.

Shipping the loader rather than resolving the document locally and shipping the
JSON keeps the partner data off this machine and out of the command line, keeps
the payload small (a Drive document is much bigger than the 2 KB that reads
it), and means the demo can be seeded before the branch is deployed -- which is
the state it will be in every time until it lands. Each file is compiled as its
own unit, so ``seed_data.py``'s ``from __future__`` import is legal and the two
modules cannot collide over a name.

## The worker accepts no host

`DJANGO_ALLOWED_HOSTS` is pinned to `*` in `deploy/task-definitions/web.json`
and is absent from the worker's, so on the worker `ALLOWED_HOSTS` is `[]` with
`DEBUG` false -- Django refuses every host before any view. That is invisible
to everything except the one part of this seed that goes through a URL: the
rows the partner entered through its own link, which must be POSTed to the
public page. The payload gives the process back the public host for the life
of the seed and says so on stdout. The durable fix is that env var in the
worker's task definition, which needs a deploy.

## Running it twice

There is no reset. ``SupplyDataAccess.purge()`` is the domain's own wholesale
delete and it refuses a program with no REGISTERED labs-only opportunity under
it (``supply_chain/scopes.py``) -- which all four of these are. A second run
would therefore not replace the chain, it would add a second one beside it in
the same program, with the same story told twice on the same screen.

So the payload REFUSES to write into a scope that already holds supply rows,
before the first write. Clearing one means deleting its rows deliberately, or
registering a labs-only opportunity under it so ``purge()`` will take it.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
OUTPUTS = HERE / "outputs.json"
LOADER = REPO / "connect_labs" / "labs" / "synthetic" / "seed_data.py"

CLUSTER = "labs-jj-cluster"
SERVICE = "labs-jj-worker"
# The worker, not the web task: this is a long batch of writes and the web
# tier is serving requests.
CONTAINER = "worker"
REGION = "us-east-1"
MARK = "OES_DEMO_RESULT"

# ECS exec puts the whole command on the container's argv. Measured against the
# labs worker: 120,101 characters ran, 131,101 came back "fork/exec
# /usr/local/bin/python: argument list too long" and 173,441 was refused by the
# API itself ("request is too large"). So the real ceiling is the 128 KiB argv
# limit, and this stops short of it with a message that names the cause --
# otherwise a seeder that grows past it fails as a shell error nobody reads as
# "the payload got too big".
MAX_COMMAND = 120_000


def _aws(*args: str, stdin=None, timeout: int = 1200) -> subprocess.CompletedProcess:
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
        timeout=120,
    )
    if result.returncode != 0:
        sys.exit(f"could not list labs worker tasks (is the labs AWS session live?): {result.stderr.strip()}")
    tasks = result.stdout.split()
    if not tasks:
        sys.exit("no running labs worker task")
    return tasks[-1]


# The driver: what runs inside labs once both modules are in memory. Kept as a
# template with literal markers rather than a format string so nothing in it
# has to be escaped twice.
DRIVER = """
import base64
import json
from urllib.parse import urlparse

from django.conf import settings

_loader = {}
exec(compile(base64.b64decode("__LOADER_B64__").decode(), "seed_data.py", "exec"), _loader)
_seed = {}
exec(compile(base64.b64decode("__SEEDER_B64__").decode(), "seed_remote.py", "exec"), _seed)

from connect_labs.supply_chain.models import Commodity, Contract, Round, SupplyPoint, scope_key

# The worker accepts no host at all, so give it back the one the web tier has.
#
# `DJANGO_ALLOWED_HOSTS` is pinned to "*" in deploy/task-definitions/web.json
# and is ABSENT from the worker's, so on this task `ALLOWED_HOSTS` is [] and
# `DEBUG` is False -- which means Django refuses EVERY host in `get_host()`,
# before any view. Tier 3 (what the partner entered through its own link) has
# to go through the public page with `django.test.Client`, because that is
# what makes `recorded_by_org` theirs rather than ours, so with nothing
# accepted the page answers a bare 400 and the seed stops having written two
# of the three tiers. `_link_host()` cannot choose its way out of this: there
# is no accepted host to choose.
#
# Accepting this deployment's own public host for the life of this process is
# not a product change -- it is the setting the web tier already runs with,
# and nothing here serves a request. The durable fix is `DJANGO_ALLOWED_HOSTS`
# in deploy/task-definitions/worker.json, which needs a deploy.
if not list(settings.ALLOWED_HOSTS or []):
    _host = urlparse(getattr(settings, "LABS_PUBLIC_URL", "") or "").hostname
    if not _host:
        raise SystemExit(
            "this worker accepts no host (ALLOWED_HOSTS is empty) and LABS_PUBLIC_URL names none "
            "either, so the partner's page cannot be reached and tier 3 cannot be seeded"
        )
    settings.ALLOWED_HOSTS = [_host]
    print("worker ALLOWED_HOSTS was empty; accepting %s for this seed" % _host)

SCOPES = _seed["SCOPES"]

# Nothing is written until every scope is known to be empty. There is no
# purge for these programs (see ensure_demo.py, "Running it twice"), so
# seeding on top of an existing chain would tell the same story twice rather
# than replace it.
occupied = []
for _name, _scope in SCOPES.items():
    _pid = _scope["program_id"]
    _rows = (
        Round.objects.filter(program_id=_pid).count()
        + Contract.objects.filter(program_id=_pid).count()
        + SupplyPoint.objects.filter(program_id=_pid).count()
        + Commodity.objects.filter(scope_key=scope_key(program_id=_pid)).count()
    )
    if _rows:
        occupied.append("%s (program %d): %d rows" % (_name, _pid, _rows))
if occupied:
    raise SystemExit(
        "refusing to seed: these scopes already hold supply rows -- "
        + "; ".join(occupied)
        + ". There is no purge for an unregistered labs-only program, so this "
        "would add a second chain beside the first rather than replace it. "
        "Delete those rows deliberately, or register a labs-only opportunity "
        "under the program so SupplyDataAccess.purge() will take it."
    )

data = _loader["load_seed_data"]("__FOLDER__", filename="__FILENAME__")

scopes = _seed["seed_scopes"](data)

chc = scopes["chc"]
chc_chain = _seed["seed_chc_chain"](chc["access"], data, chc["reference"])
# Tier 3 -- the rows the distributor entered itself -- is seeded by this, and
# it has to go THROUGH the link, so it follows the chain it covers.
links = _seed["seed_partner_links"](chc["access"], data, chc["reference"], chc_chain)

supply_only = _seed["seed_supply_only"](data, scopes)

# The RUTF and chlorine chains are tasks 8 and 10. Their scopes already hold
# their own catalogues (seed_scopes above); the chain seeders plug in here.

print(
    "__MARK__"
    + json.dumps(
        {
            "programs": {name: scope["program_id"] for name, scope in SCOPES.items()},
            "orders": {
                "chc": chc_chain["contract"]["id"],
                "supply_only": supply_only["chain"]["contract"]["id"],
            },
            "rounds": {
                "chc": chc_chain["round"]["id"],
                "supply_only": supply_only["chain"]["round"]["id"],
            },
            "partner_links": {
                slug: {"id": link["id"], "url": link["url"]} for slug, link in links.items()
            },
        }
    )
    + "__MARK__"
)
"""


def build_command(folder: str, filename: str) -> str:
    loader = base64.b64encode(LOADER.read_bytes()).decode()
    seeder = base64.b64encode((HERE / "seed_remote.py").read_bytes()).decode()
    driver = (
        DRIVER.replace("__LOADER_B64__", loader)
        .replace("__SEEDER_B64__", seeder)
        .replace("__FOLDER__", folder)
        .replace("__FILENAME__", filename)
        .replace("__MARK__", MARK)
    )
    encoded = base64.b64encode(driver.encode()).decode()
    command = f"python manage.py shell -c \"exec(__import__('base64').b64decode('{encoded}').decode())\""
    if len(command) > MAX_COMMAND:
        sys.exit(
            f"the payload is {len(command):,} characters, past the {MAX_COMMAND:,} this route can "
            "carry (ECS exec puts it on the container's argv, which stops at 128 KiB). The seeder "
            "has outgrown being shipped on a command line: write it to the worker's disk, or fetch "
            "it there, instead of growing this."
        )
    return command


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--drive-folder",
        required=True,
        help="the Drive folder id holding the seed document (the partner names, volumes and prices "
        "are not in this repository)",
    )
    parser.add_argument("--filename", default="oes-demo.json", help="the document in that folder")
    args = parser.parse_args()

    if not LOADER.exists():
        sys.exit(f"no Drive loader at {LOADER} -- the payload ships it, so it has to be here")

    command = build_command(args.drive_folder, args.filename)
    task = _worker_task()
    print(f"seeding the OES demo on {task.rsplit('/', 1)[-1]} ({len(command):,} char payload)…", flush=True)

    # The interactive session needs a live stdin for the WHOLE run: /dev/null
    # gives "Cannot perform start session: EOF" and the command silently does
    # not run. A sleeping subprocess holds it open.
    holder = subprocess.Popen(["sleep", "1800"], stdout=subprocess.PIPE)
    try:
        result = _aws(
            "ecs",
            "execute-command",
            "--cluster",
            CLUSTER,
            "--task",
            task,
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
        tail = "\n".join(output.splitlines()[-60:])
        sys.exit(f"seed did not report a result (exit {result.returncode}):\n{tail}")
    realized = json.loads(match.group(1).replace("\r", "").replace("\n", ""))

    OUTPUTS.write_text(json.dumps(realized, indent=2) + "\n")
    programs = realized["programs"]
    print("seeded:")
    print("  programs      " + ", ".join(f"{name} {pid}" for name, pid in programs.items()))
    print("  orders        " + ", ".join(f"{name} {oid}" for name, oid in realized["orders"].items()))
    print(f"  partner links {len(realized['partner_links'])} (URLs carry raw tokens — in {OUTPUTS.name} only)")
    print(f"outputs → {OUTPUTS}")


if __name__ == "__main__":
    main()
