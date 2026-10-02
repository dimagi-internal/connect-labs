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

HERE = Path(__file__).resolve().parent
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


# ---------------------------------------------------------------------------
# Sahel answers the chase: the AI records the reply over the labs MCP, off camera
# ---------------------------------------------------------------------------

OUTPUTS = HERE / "outputs.json"
MCP_URL = os.environ.get("LABS_MCP_URL", "https://labs.connect.dimagi.com/mcp/")

SAHEL_REPLY = {
    "ref": "<a82c4-r2@mail.sahel-nutrition.example.invalid>",
    "excerpt": (
        "Apologies for the late reply. We can offer 300,000 sachets (2,000 cartons of 150 x 92 g) at "
        "EUR 0.31 per sachet, EXW Niamey. Transport to Kano can be arranged at your cost; freight estimate on "
        "request. Shelf life 24 months, lead time 5 weeks, minimum order 500 cartons. Valid 30 days."
    ),
    "sender": "Amadou Issoufou, Sahel Nutrition Industries",
}


def _mcp_token() -> str:
    """The labs MCP token the AI uses: LABS_MCP_TOKEN, else the connect_labs server in ~/.claude.json."""
    tok = os.environ.get("LABS_MCP_TOKEN")
    if tok:
        return tok
    cfg = Path.home() / ".claude.json"
    servers = json.loads(cfg.read_text()).get("mcpServers") or {} if cfg.exists() else {}
    for name, spec in servers.items():
        auth = (spec.get("headers") or {}).get("Authorization") or ""
        if "labs" in name and auth.startswith("Bearer "):
            return auth[len("Bearer ") :]
    sys.exit("no labs MCP token (set LABS_MCP_TOKEN, or add the connect_labs MCP server to ~/.claude.json)")


class Mcp:
    """A minimal streamable-HTTP MCP client: initialize once, then tools/call."""

    def __init__(self):
        import urllib.request

        self._urllib = urllib.request
        self.headers = {
            "Authorization": f"Bearer {_mcp_token()}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        _, headers = self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "supply-sophie-unanswered-round", "version": "1"},
                },
            }
        )
        if headers.get("mcp-session-id"):
            self.headers["Mcp-Session-Id"] = headers["mcp-session-id"]
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _post(self, payload):
        request = self._urllib.Request(MCP_URL, data=json.dumps(payload).encode(), headers=self.headers, method="POST")
        with self._urllib.urlopen(request, timeout=120) as response:
            return response.read().decode(), {k.lower(): v for k, v in response.headers.items()}

    def call(self, name: str, **arguments):
        body, _ = self._post(
            {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
        )
        for line in body.splitlines():
            if line.startswith("data:"):
                body = line[5:].strip()
                break
        result = json.loads(body).get("result") or {}
        if result.get("isError"):
            raise RuntimeError(f"{name} refused: {result.get('content')}")
        return result


def sahel_replies() -> None:
    """The reply arrives after Sophie's chase; the AI records it as it would any forwarded email."""
    import datetime as dt

    out = json.loads(OUTPUTS.read_text())
    today = dt.date.today().isoformat()
    mcp = Mcp()
    source = dict(SAHEL_REPLY)
    mcp.call(
        "supply_chain_quote_record",
        program_id=out["program_id"],
        source=source,
        data={
            "tender_id": out["round2_tender_id"],
            "supplier_id": out["sahel_supplier_id"],
            "commodity_slug": "rutf",
            "as_quoted_amount": "0.31",
            "as_quoted_unit": "per_base_unit",
            "as_quoted_currency": "EUR",
            "quantity_basis": 300000,
            "quantity_basis_unit": "sachet",
            "pack_spec_source": "stated_on_quote",
            "base_per_pack_stated": 150,
            "base_unit_grams_stated": 92,
            "freight_basis": "excluded",
            "duties_basis": "excluded",
            "shelf_life_months_stated": 24,
            "lead_time_days": 35,
            "moq": 500,
            "moq_unit": "carton",
            "incoterm": "EXW Niamey",
            "delivery_point_keys": ["kano"],
            "validity_until": (dt.date.today() + dt.timedelta(days=30)).isoformat(),
            "received_on": today,
        },
    )
    mcp.call(
        "supply_chain_outreach_update",
        program_id=out["program_id"],
        outreach_id=out["sahel_outreach_id"],
        source=source,
        data={"responded": True, "response_kind": "quote", "responded_on": today},
    )
    print("recorded Sahel's reply over MCP")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--outputs")
    parser.add_argument(
        "--sahel-replies",
        action="store_true",
        help="record Sahel's reply to the chase over the labs MCP (scene 3's before: hook)",
    )
    args = parser.parse_args()
    if args.sahel_replies:
        sahel_replies()
        return
    result = seed_via_ecs() if _aws_live() else seed_via_github()
    write_storage_state(result.pop("sophie_session"))
    if args.outputs:
        Path(args.outputs).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
