"""Setup for the `supply-sophie-rutf` DDD walkthrough (labs-only program 10672).

The RUTF program's story is dated history -- round 1 replayed from its first
quote to its payment, round 2 open with three quotes that cannot yet be
compared -- and the walkthrough then CHANGES it on camera: a supplier answers
between the comparison scene and the next one, and Sophie awards round 2. So
every take needs the program back where the story starts.

Three things happen here.

1. **Reset + seed, one ECS exec on the labs worker** (AWS profile `labs`).
   `SupplyDataAccess.purge()` for program 10672 ONLY -- refused if any update
   link exists in it, because a link may have been sent to someone -- and then
   the RUTF scope alone is seeded with the same `seed_remote.py` functions
   `oes-demo/ensure_demo.py` uses (`seed_scopes` restricted to `rutf`, then
   `seed_rutf_rounds`). The other programs of the OES demo are not touched.
   The backdated history needs the seed-only `recorded_at` override, which
   only exists in-process, so this cannot go over MCP.

2. **Outputs** for the recorder's `${var}` substitution (gitignored file).

3. **The supplier's answer, between the comparison scene and the next.** The
   recipe runs `seed.py --answer-now` as that scene's `before:` hook, which the
   recorder (and `recipe_preflight`) runs off camera after the comparison is
   filmed. It records the supplier's reply exactly the way the story says it
   arrives: the ACE agent calls `quote_correct` over the labs MCP with the
   reply as its source (`source.ref` is the reply's Message-ID, so a second
   forwarding replays rather than correcting twice). Nothing is staged: it is
   the product's own agent path, run by the agent account.

The partner data is not in this repository: it is read on the worker from the
private Drive seed document. Set `OES_DEMO_DRIVE_FOLDER` (or put the folder id
in `.drive-folder` beside this file -- gitignored).

    python3 scripts/walkthroughs/supply-sophie-rutf/seed.py --outputs <file>
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
import urllib.request
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "oes-demo"))
import ensure_demo as oes  # noqa: E402

PROGRAM_ID = 10672
AS_OF_DATE = "2026-08-20"
MARK = "SOPHIE_RUTF_RESULT"
SIDECAR = HERE / ".clarification.json"
MCP_URL = os.environ.get("LABS_MCP_URL", "https://labs.connect.dimagi.com/mcp/")

DRIVER = """
import base64
import json

__MODULES__

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.update_links.models import UpdateLink

PID = __PID__
links = UpdateLink.objects.filter(program_id=PID).count()
if links:
    raise SystemExit("REFUSED: program %d holds %d update link(s); not purging" % (PID, links))
print("purged", SupplyDataAccess(access_token="sophie-walkthrough-reset", program_id=PID, caller=SYSTEM).purge())

# The RUTF scope alone. seed_scopes and the market check read the module's
# SCOPES, so narrowing it here keeps every other OES program untouched.
_seed["SCOPES"] = {"rutf": _seed["SCOPES"]["rutf"]}
assert _seed["SCOPES"]["rutf"]["program_id"] == PID

data = _loader["load_seed_data"]("__FOLDER__", filename="__FILENAME__")

# The story's dates and round 2's request come from the document, so check
# here that they make the points the scenes make, before anything is written.
# Only the failed check is named: this route's log can be public.
import datetime as _dt
_rounds = data.get("rutf_rounds") or {}
_ship = (_rounds.get("round_one") or {}).get("shipment") or {}
_two = _rounds.get("round_two") or {}
_out = _two.get("outreach") or {}
_asof = "__ASOF__"
_opens = _out.get("sent_on") or min([q.get("received_on") or "9999" for q in _two.get("quotes") or []] or ["9999"])
_notes = ((_two.get("round") or {}).get("notes_to_supplier") or "").lower()
_checks = {
    "round 1 dispatched on or before the as-of date": bool(_ship.get("dispatched_on"))
    and _ship["dispatched_on"] <= _asof,
    "round 1's ETA slip states both ETAs": bool(_ship.get("eta_original") and _ship.get("expected_on"))
    and _ship["eta_original"] != _ship["expected_on"],
    "the ETA slip was learned after the as-of date": (_ship.get("eta_slip_learned_on") or "") > _asof,
    "round 2 opened after the as-of date": _opens > _asof,
    "round 2's requests went out 14 or more days ago": bool(_out.get("sent_on"))
    and _dt.date.fromisoformat(_out["sent_on"]) <= _dt.date.today() - _dt.timedelta(days=14),
    "round 2 has a supplier who never answered": bool(_out.get("non_responders")),
    "round 2 asks suppliers for sachets per carton, freight and duties": all(
        word in _notes for word in ("sachet", "freight", "dut")
    ),
}
_failed = [name for name, ok in _checks.items() if not ok]
if _failed:
    raise SystemExit("REFUSED: the seed document does not make the story's points: " + "; ".join(_failed))
scopes = _seed["seed_scopes"](data)
rounds = _seed["seed_rutf_rounds"](data, scopes)

access = scopes["rutf"]["access"]
clar = _seed["without_commentary"]((data["rutf_rounds"]["round_two"] or {}).get("clarification") or {})
pack_missing = None
excerpt = None
if clar:
    for quote in rounds["round_two"]["quotes"]:
        supplier = next(s for s in rounds["round_two"]["suppliers"] if s["id"] == quote["supplier_id"])
        if supplier["name"] == clar["supplier_label"]:
            pack_missing = quote
    excerpt = _seed["_clarification_excerpt"](access, pack_missing, clar["corrections"])

# Sophie signs in off camera. She is the story's program manager and a demo
# user of this environment only (no Connect account), so there is no OAuth for
# her: the session a real sign-in would produce is minted here instead, the
# way the recorder's off-camera personas work. Scoped to exactly this user,
# this synthetic program's opportunity, and 12 hours.
import time as _time
from importlib import import_module
from django.conf import settings
from django.contrib.auth import get_user_model
from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.labs.synthetic.org_tree import synthetic_program_id

sophie = _seed["demo_persona_users"]()["sophie"]
if sophie.is_staff or sophie.is_superuser or not sophie.email.endswith("@example.invalid"):
    raise SystemExit("REFUSED: %s is not a demo-only persona" % sophie.username)
opps = [o for o in SyntheticOpportunity.objects.filter(labs_only=True, enabled=True) if synthetic_program_id(o) == PID]
if not opps:
    raise SystemExit("REFUSED: no registered labs-only opportunity under program %d" % PID)
for opp in opps:
    domains = list(opp.allowed_domains or [])
    if domains and "@example.invalid" not in domains:
        opp.allowed_domains = domains + ["@example.invalid"]
        opp.save(update_fields=["allowed_domains"])
if not sophie.view_synthetic_opps:
    sophie.view_synthetic_opps = True
    sophie.save(update_fields=["view_synthetic_opps"])
_hours = 12
MINT = __MINT__
store = import_module(settings.SESSION_ENGINE).SessionStore()
store["_auth_user_id"] = str(sophie.pk)
store["_auth_user_backend"] = "django.contrib.auth.backends.ModelBackend"
store["_auth_user_hash"] = sophie.get_session_auth_hash()
store["labs_oauth"] = {
    "access_token": "demo-persona-no-connect-account",
    "refresh_token": "",
    "expires_at": _time.time() + _hours * 3600,
    "organization_data": {"organizations": [], "programs": [], "opportunities": []},
}
store.set_expiry(_hours * 3600)
if MINT:
    store.create()

_session = {"key": store.session_key, "expires": int(_time.time() + _hours * 3600)} if MINT else None
SEAL = __SEAL__
if _session and SEAL:
    # The GitHub route's log is public: the session leaves sealed to a key
    # that exists only on the machine that asked for it.
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    _pub = serialization.load_pem_public_key(base64.b64decode(SEAL))
    _session = {"sealed": base64.b64encode(_pub.encrypt(json.dumps(_session).encode(), padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None))).decode()}
print("__MARK__" + json.dumps({
    "sophie_session": _session,
    "program_id": PID,
    "round1_tender_id": rounds["round_one"]["round"]["id"],
    "round2_tender_id": rounds["round_two"]["round"]["id"],
    "quote_pack_missing_id": pack_missing["id"] if pack_missing else None,
    "clarification": {
        "corrections": clar.get("corrections"),
        "excerpt": excerpt,
        "ref": "<rutf-r2-clarification@demo.invalid>",
    } if clar else None,
}, default=str) + "__MARK__")
"""


def _folder() -> str:
    folder = os.environ.get("OES_DEMO_DRIVE_FOLDER", "").strip()
    local = HERE / ".drive-folder"
    if not folder and local.exists():
        folder = local.read_text().strip()
    if not folder:
        sys.exit("set OES_DEMO_DRIVE_FOLDER (or write the folder id to .drive-folder beside this script)")
    return folder


INLINE_MODULES = """
_loader = {}
exec(compile(base64.b64decode("__LOADER_B64__").decode(), "seed_data.py", "exec"), _loader)
_seed = {}
exec(compile(base64.b64decode("__SEEDER_B64__").decode(), "seed_remote.py", "exec"), _seed)
"""

# The GitHub route cannot carry the modules: ECS run-task overrides stop at 8 KB.
# The repository is public, so the task fetches them at a pinned commit instead.
FETCHED_MODULES = """
import urllib.request
def _fetch(path):
    url = "https://raw.githubusercontent.com/dimagi-internal/connect-labs/__SHA__/" + path
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read().decode()
_loader = {}
exec(compile(_fetch("connect_labs/labs/synthetic/seed_data.py"), "seed_data.py", "exec"), _loader)
_seed = {}
exec(compile(_fetch("scripts/walkthroughs/oes-demo/seed_remote.py"), "seed_remote.py", "exec"), _seed)
"""


def _driver(filename: str, *, modules: str, mint: bool, seal: str = "") -> str:
    return (
        DRIVER.replace("__MODULES__", modules)
        .replace("__SEAL__", repr(seal))
        .replace("__LOADER_B64__", base64.b64encode(oes.LOADER.read_bytes()).decode())
        .replace("__SEEDER_B64__", base64.b64encode((oes.HERE / "seed_remote.py").read_bytes()).decode())
        .replace("__FOLDER__", _folder())
        .replace("__FILENAME__", filename)
        .replace("__MARK__", MARK)
        .replace("__PID__", str(PROGRAM_ID))
        .replace("__ASOF__", AS_OF_DATE)
        .replace("__MINT__", "True" if mint else "False")
    )


def _parse_result(output: str, why: str) -> dict:
    match = re.search(MARK + r"(\{.*?\})" + MARK, output, re.S)
    if not match:
        tail = "\n".join(output.splitlines()[-40:])
        sys.exit(f"seed printed no result ({why}):\n{tail}")
    return json.loads(match.group(1).replace("\r", "").replace("\n", ""))


def _aws_session_live() -> bool:
    probe = subprocess.run(
        ["aws", "sts", "get-caller-identity", "--profile", os.environ.get("AWS_PROFILE", "labs")],
        capture_output=True,
        text=True,
    )
    return probe.returncode == 0


def reset_and_seed_via_github(filename: str) -> dict:
    """Same payload, run as a one-off task by the repo's run-labs-command workflow.

    For when the local AWS SSO session has expired (renewing it needs a person).
    That workflow assumes its role through GitHub OIDC. Its log is PUBLIC, so
    Sophie's session is minted SEALED: a throwaway RSA key pair is made here, the
    task encrypts the session to the public half, and only this machine can open
    it (``_unseal``). Nothing usable is printed.
    """
    sha = subprocess.run(
        ["git", "rev-parse", "origin/main"], cwd=HERE, capture_output=True, text=True, check=True
    ).stdout.strip()
    seal = base64.b64encode(_seal_public_key()).decode()
    driver = _driver(filename, modules=FETCHED_MODULES.replace("__SHA__", sha), mint=True, seal=seal)
    packed = base64.b64encode(zlib.compress(driver.encode(), 9)).decode()
    command = (
        "shell -c \"exec(__import__('zlib').decompress(" f"__import__('base64').b64decode('{packed}')).decode())\""
    )
    if len(command) > 7000:
        sys.exit(f"payload {len(command):,} chars is past what ECS run-task overrides carry (8 KB)")
    repo = "dimagi-internal/connect-labs"
    started = time.time()
    subprocess.run(
        [
            "gh",
            "workflow",
            "run",
            "run-labs-command.yml",
            "--repo",
            repo,
            "--ref",
            "main",
            "--field",
            f"command={command}",
        ],
        check=True,
        capture_output=True,
    )
    print("resetting + seeding over the run-labs-command workflow (AWS SSO expired)…", file=sys.stderr, flush=True)
    run_id = None
    for _ in range(30):
        time.sleep(4)
        runs = json.loads(
            subprocess.run(
                [
                    "gh",
                    "run",
                    "list",
                    "--repo",
                    repo,
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
    subprocess.run(["gh", "run", "watch", run_id, "--repo", repo], capture_output=True, timeout=1500)
    log = subprocess.run(["gh", "run", "view", run_id, "--repo", repo, "--log"], capture_output=True, text=True)
    return _parse_result(log.stdout + log.stderr, f"run {run_id}")


SEAL_KEY = HERE / ".seal-key.pem"


def _seal_public_key() -> bytes:
    """A fresh RSA key pair (gitignored private half); returns the public PEM."""
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


def reset_and_seed(filename: str) -> dict:
    if not _aws_session_live():
        return reset_and_seed_via_github(filename)
    driver = _driver(filename, modules=INLINE_MODULES, mint=True)
    packed = base64.b64encode(zlib.compress(driver.encode(), 9)).decode()
    command = (
        "python manage.py shell -c \"exec(__import__('zlib').decompress("
        f"__import__('base64').b64decode('{packed}')).decode())\""
    )
    if len(command) > oes.MAX_COMMAND:
        sys.exit(f"payload {len(command):,} chars exceeds the ECS exec argv limit")
    task = oes._worker_task()
    print(f"resetting + seeding program {PROGRAM_ID} on {task.rsplit('/', 1)[-1]}…", file=sys.stderr, flush=True)
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
    return _parse_result(result.stdout + result.stderr, f"exit {result.returncode}")


STORAGE_STATE = HERE / ".sophie-storage-state.json"


def write_storage_state(session: dict) -> None:
    """Playwright storage state for Sophie's session (gitignored; the key is a credential)."""
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
# The supplier's answer, recorded by the agent over MCP before the answer scene
# ---------------------------------------------------------------------------


def _token() -> str:
    tok = os.environ.get("LABS_MCP_TOKEN")
    if tok:
        return tok
    cfg = Path.home() / ".claude.json"
    for name, spec in (json.loads(cfg.read_text()).get("mcpServers") or {}).items():
        auth = (spec.get("headers") or {}).get("Authorization") or ""
        if "labs" in name and auth.startswith("Bearer "):
            return auth[len("Bearer ") :]
    sys.exit("no labs MCP token (LABS_MCP_TOKEN or connect_labs in ~/.claude.json)")


class Mcp:
    def __init__(self):
        self.headers = {
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        _, headers = self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "supply-sophie-rutf", "version": "1"},
                },
            }
        )
        if headers.get("mcp-session-id"):
            self.headers["Mcp-Session-Id"] = headers["mcp-session-id"]
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _post(self, payload):
        request = urllib.request.Request(
            MCP_URL, data=json.dumps(payload).encode(), headers=self.headers, method="POST"
        )
        with urllib.request.urlopen(request, timeout=120) as response:
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


def record_answer(mcp: Mcp, realized: dict, clarification: dict) -> dict:
    return mcp.call(
        "supply_chain_quote_correct",
        program_id=PROGRAM_ID,
        quote_id=int(realized["quote_pack_missing_id"]),
        data=clarification["corrections"],
        reason="The supplier answered the question the comparison raised.",
        source={"ref": clarification["ref"], "excerpt": clarification["excerpt"]},
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--outputs", required=False)
    parser.add_argument("--filename", default="oes-demo.json")
    parser.add_argument(
        "--answer-now",
        action="store_true",
        help="record the supplier's answer now (the answer scene's before: hook)",
    )
    args = parser.parse_args()

    if args.answer_now:
        state = json.loads(SIDECAR.read_text())
        print(json.dumps(record_answer(Mcp(), state["realized"], state["clarification"]))[:500])
        return

    realized = reset_and_seed(args.filename)
    clarification = realized.pop("clarification")
    session = realized.pop("sophie_session")
    if session and "sealed" in session:
        session = _unseal(session["sealed"])
    if session:
        write_storage_state(session)
    else:
        state = json.loads(STORAGE_STATE.read_text()) if STORAGE_STATE.exists() else {"cookies": []}
        left = min((c.get("expires", 0) for c in state["cookies"]), default=0) - time.time()
        if left < 3600:
            sys.exit(
                "no fresh Sophie session and the last one expires within the hour; "
                "renew AWS SSO (aws sso login --profile labs)"
            )
        print(f"reusing Sophie's session ({left / 3600:.1f} h left)", file=sys.stderr)
    if not realized.get("quote_pack_missing_id") or not clarification:
        sys.exit("the seed document names no round-2 clarification; scene 5 has nothing to show")
    outputs = {**realized, "as_of_date": AS_OF_DATE}
    SIDECAR.write_text(json.dumps({"realized": realized, "clarification": clarification}))
    if args.outputs:
        Path(args.outputs).write_text(json.dumps(outputs, indent=2) + "\n")
    print(json.dumps(outputs))


if __name__ == "__main__":
    main()
