"""Setup for the `supply-sophie-unanswered-round` DDD walkthrough (its own labs-only program).

Every take needs the world back where the story starts, because the
walkthrough changes it on camera (Sophie records a chase and answers a
question). So before each render this:

1. **Resets and seeds, server-side.** `replay.run()` executes inside the labs
   app: it registers the walkthrough's labs-only program once, purges THAT
   program only (refused if an update link exists in it, and refused outright
   for the other demos' programs), and replays the round as dated history --
   Sophie's steps over `web`, every supplier and forwarder email as the ACE
   agent over `mcp` with `source = {ref, excerpt, sender}`. Dated writes need
   the seed-only `recorded_at` override, which exists only in-process, so
   this cannot go over the MCP.

   Two routes, the same code: ECS Exec on the labs worker when the AWS SSO
   session for profile `labs` is live, else the repo's `run-labs-command`
   workflow (GitHub OIDC). The workflow's task fetches `replay.py` at the
   pinned commit of `origin/main` -- so the replay must be merged before it
   can seed -- and its log is PUBLIC, so Sophie's session leaves it sealed to
   a key that exists only on this machine.

2. **Writes the outputs** the recorder substitutes (`${program_id}`, ...) and
   Sophie's Playwright storage state (gitignored: the key is a credential).

    python3 scripts/walkthroughs/supply-sophie-unanswered-round/seed.py --outputs <file>

**Local mode** (canopy's DDD inner loop, `make serve-demo`): the same
`replay.run()`, in-process against the local build's own database, with the
sessions minted by `connect_labs.labs.demo_sessions` (local DEBUG builds only)
for a loopback cookie -- Sophie's, and the agent account's beside it. Chosen by
`--local` / `--base-url http://localhost:<port>`, or automatically when the
recorder says it is rendering against a loopback origin
(`CANOPY_RENDER_BASE_URL`). The outputs come out in the same shape.

    python3 scripts/walkthroughs/supply-sophie-unanswered-round/seed.py --local --outputs <file>
"""

from __future__ import annotations

import argparse
import base64
import calendar
import json
import os
import re
import subprocess
import sys
import time
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _lib import local_seed  # noqa: E402

HERE = Path(__file__).resolve().parent
AGENT_STORAGE_STATE = HERE / ".ace-storage-state.json"
REPO = "dimagi-internal/connect-labs"
MARK = "UNANSWERED_ROUND_RESULT"
STORAGE_STATE = HERE / ".sophie-storage-state.json"
SEAL_KEY = HERE / ".seal-key.pem"
REPLAY_PATH = "scripts/walkthroughs/supply-sophie-unanswered-round/replay.py"

# Runs inside `manage.py shell` on labs. __LOAD__ binds `replay` (a module namespace dict).
DRIVER = """
import base64, json
__LOAD__
out = replay["run"](mint=True)
session = out.pop("sophie_session")
SEAL = __SEAL__
if SEAL:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    key = serialization.load_pem_public_key(base64.b64decode(SEAL))
    session = {"sealed": base64.b64encode(key.encrypt(json.dumps(session).encode(), padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None))).decode()}
out["sophie_session"] = session
print("__MARK__" + json.dumps(out, default=str) + "__MARK__")
"""

INLINE = """
replay = {"__name__": "replay"}
exec(compile(base64.b64decode("__REPLAY_B64__").decode(), "replay.py", "exec"), replay)
"""

FETCHED = """
import urllib.request
with urllib.request.urlopen("https://raw.githubusercontent.com/__REPO__/__SHA__/__PATH__", timeout=60) as r:
    _src = r.read().decode()
replay = {"__name__": "replay"}
exec(compile(_src, "replay.py", "exec"), replay)
"""


def _driver(load: str, seal: str = "") -> str:
    return DRIVER.replace("__LOAD__", load).replace("__SEAL__", repr(seal)).replace("__MARK__", MARK)


def _parse(output: str, why: str) -> dict:
    match = re.search(MARK + r"(\{.*?\})" + MARK, output, re.S)
    if not match:
        tail = "\n".join(output.splitlines()[-40:])
        sys.exit(f"seed printed no result ({why}):\n{tail}")
    return json.loads(match.group(1).replace("\r", "").replace("\n", ""))


def _aws_live() -> bool:
    probe = subprocess.run(
        ["aws", "sts", "get-caller-identity", "--profile", os.environ.get("AWS_PROFILE", "labs")],
        capture_output=True,
        text=True,
    )
    return probe.returncode == 0


def _seal_public_key() -> bytes:
    SEAL_KEY.unlink(missing_ok=True)
    subprocess.run(
        ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(SEAL_KEY)],
        check=True,
        capture_output=True,
    )
    os.chmod(SEAL_KEY, 0o600)
    return subprocess.run(["openssl", "pkey", "-in", str(SEAL_KEY), "-pubout"], check=True, capture_output=True).stdout


def _unseal(sealed: str) -> dict:
    try:
        opened = subprocess.run(
            [
                "openssl",
                "pkeyutl",
                "-decrypt",
                "-inkey",
                str(SEAL_KEY),
                "-pkeyopt",
                "rsa_padding_mode:oaep",
                "-pkeyopt",
                "rsa_oaep_md:sha256",
                "-pkeyopt",
                "rsa_mgf1_md:sha256",
            ],
            input=base64.b64decode(sealed),
            check=True,
            capture_output=True,
        ).stdout
    finally:
        SEAL_KEY.unlink(missing_ok=True)
    return json.loads(opened)


def seed_via_github() -> dict:
    sha = subprocess.run(
        ["git", "rev-parse", "origin/main"], cwd=HERE, capture_output=True, text=True, check=True
    ).stdout.strip()
    probe = f"https://raw.githubusercontent.com/{REPO}/{sha}/{REPLAY_PATH}"
    if subprocess.run(["curl", "-sfI", probe], capture_output=True).returncode != 0:
        sys.exit(f"{REPLAY_PATH} is not on origin/main ({sha[:9]}); merge it before seeding over GitHub")
    load = FETCHED.replace("__REPO__", REPO).replace("__SHA__", sha).replace("__PATH__", REPLAY_PATH)
    driver = _driver(load, seal=base64.b64encode(_seal_public_key()).decode())
    packed = base64.b64encode(zlib.compress(driver.encode(), 9)).decode()
    command = f"shell -c \"exec(__import__('zlib').decompress(__import__('base64').b64decode('{packed}')).decode())\""
    if len(command) > 7000:
        sys.exit(f"payload {len(command):,} chars is past what ECS run-task overrides carry (8 KB)")
    started = time.time()
    subprocess.run(
        [
            "gh",
            "workflow",
            "run",
            "run-labs-command.yml",
            "--repo",
            REPO,
            "--ref",
            "main",
            "--field",
            f"command={command}",
        ],
        check=True,
        capture_output=True,
    )
    print("seeding over the run-labs-command workflow…", file=sys.stderr, flush=True)
    run_id = None
    for _ in range(40):
        time.sleep(4)
        runs = json.loads(
            subprocess.run(
                [
                    "gh",
                    "run",
                    "list",
                    "--repo",
                    REPO,
                    "--workflow",
                    "run-labs-command.yml",
                    "--limit",
                    "5",
                    "--json",
                    "databaseId,createdAt",
                ],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )
        fresh = [
            r for r in runs if calendar.timegm(time.strptime(r["createdAt"], "%Y-%m-%dT%H:%M:%SZ")) >= started - 10
        ]
        if fresh:
            run_id = str(fresh[0]["databaseId"])
            break
    if run_id is None:
        sys.exit("dispatched run-labs-command but could not find its run")
    subprocess.run(["gh", "run", "watch", run_id, "--repo", REPO], capture_output=True, timeout=1500)
    log = subprocess.run(["gh", "run", "view", run_id, "--repo", REPO, "--log"], capture_output=True, text=True)
    result = _parse(log.stdout + log.stderr, f"run {run_id}")
    result["sophie_session"] = _unseal(result["sophie_session"]["sealed"])
    return result


def seed_via_ecs() -> dict:
    sys.path.insert(0, str(HERE.parent / "oes-demo"))
    import ensure_demo as oes  # noqa: E402  (the worker-task lookup and aws wrapper)

    load = INLINE.replace("__REPLAY_B64__", base64.b64encode((HERE / "replay.py").read_bytes()).decode())
    packed = base64.b64encode(zlib.compress(_driver(load).encode(), 9)).decode()
    command = (
        "python manage.py shell -c \"exec(__import__('zlib').decompress("
        f"__import__('base64').b64decode('{packed}')).decode())\""
    )
    if len(command) > oes.MAX_COMMAND:
        sys.exit(f"payload {len(command):,} chars exceeds the ECS exec argv limit")
    task = oes._worker_task()
    print(f"seeding on {task.rsplit('/', 1)[-1]}…", file=sys.stderr, flush=True)
    holder = subprocess.Popen(["sleep", "1500"], stdout=subprocess.PIPE)
    try:
        result = oes._aws(
            "ecs",
            "execute-command",
            "--cluster",
            oes.CLUSTER,
            "--task",
            task,
            "--container",
            oes.CONTAINER,
            "--interactive",
            "--command",
            command,
            stdin=holder.stdout,
            timeout=1200,
        )
    finally:
        holder.kill()
    return _parse(result.stdout + result.stderr, f"exit {result.returncode}")


def write_storage_state(session: dict) -> None:
    STORAGE_STATE.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "sessionid",
                        "value": session["key"],
                        "domain": "labs.connect.dimagi.com",
                        "path": "/",
                        "expires": session["expires"],
                        "httpOnly": True,
                        "secure": True,
                        "sameSite": "Lax",
                    }
                ],
                "origins": [],
            }
        )
    )
    os.chmod(STORAGE_STATE, 0o600)


def seed_local(*, base_url: str, outputs: str | None = None) -> dict:
    """Inside the local labs app (Django set up): reset, seed, sign Sophie and the agent in.

    Called by `tools/ddd_demo.py` against the local build's own database. Refused
    on anything but a local DEBUG build (`demo_sessions.require_local`) before
    anything is written.
    """
    import importlib.util

    from connect_labs.labs import demo_sessions

    demo_sessions.require_local()
    spec = importlib.util.spec_from_file_location("unanswered_round_replay", HERE / "replay.py")
    replay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay)
    # A fresh local database has no buyer organisation; labs has one, so only here is it created.
    result = replay.run(create_buyer=True)
    people = replay.personas()
    local_seed.write_state(STORAGE_STATE, demo_sessions.storage_state(people["sophie"], base_url))
    local_seed.write_state(AGENT_STORAGE_STATE, demo_sessions.storage_state(people["ace"], base_url))
    Path(outputs or HERE / "outputs.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--outputs")
    local_seed.add_arguments(parser)
    args = parser.parse_args()
    local = local_seed.target(args.local, args.base_url)
    if local:
        outputs = Path(args.outputs) if args.outputs else HERE / "outputs.json"
        print(json.dumps(local_seed.run(HERE.name, local, outputs)))
        return
    result = seed_via_ecs() if _aws_live() else seed_via_github()
    write_storage_state(result.pop("sophie_session"))
    if args.outputs:
        Path(args.outputs).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
