"""End-to-end smoke test for `inv safe-claude`.

Drives `claude -p` in safe mode and exercises:
  1. workflow_get                   (read workflow + version)
  2. workflow_update_render_code    (append marker comment, bump version)
  3. workflow_get                   (verify marker is present)
  4. workflow_update_render_code    (revert — strip marker)
  5. pipeline_sql                   (read — optional, requires --pipeline-id)
  6. pipeline_preview               (read — optional)

Each step is a separate `claude -p` call using the same settings.safe.json +
rendered mcp.safe.json as `inv safe-claude`, so a pass proves the full stack
works end-to-end, not just the MCP server in isolation.

Auth: Vertex AI (project `connect-labs`, region `global`). The
service-account JSON is fetched fresh from 1Password (AI-Agents vault)
into a 0600 tempfile at launch and deleted on exit — nothing persists in
the repo or on disk across runs. If `.gcp/vertex.json` exists (from
`inv vertex-setup`), that cache is used instead and no fetch happens.
ANTHROPIC_API_KEY is stripped from the child env so PII cannot transit a
non-governed endpoint.

Requires: Claude Code CLI, `op` CLI signed in to the dimagi account (or
a cached .gcp/vertex.json), and a connect_labs PAT in ~/.claude.json
(via the `/labs-token-setup` skill).

Usage:
    python safe-claude/e2e.py --workflow-id 123 --opportunity-id 456
    python safe-claude/e2e.py --workflow-id 123 --opportunity-id 456 --pipeline-id 789
    inv safe-claude-e2e --workflow-id=123 --opportunity-id=456 [--pipeline-id=789]

Use a DISPOSABLE workflow you control. The marker comment is reverted on
success; if the script fails mid-run, a stray `/* e2e-test-<ts> */` may
remain in the JSX until you clean it up.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = REPO_ROOT / "safe-claude" / "settings.json"
MCP_TEMPLATE_PATH = REPO_ROOT / "safe-claude" / "mcp.json"


def _load_env(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _read_user_mcp_pat() -> str | None:
    """`claude mcp add --scope user` writes to ~/.claude.json, not
    ~/.claude/mcp.json. Check both for resilience."""
    for p in (Path.home() / ".claude.json", Path.home() / ".claude" / "mcp.json"):
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        auth = data.get("mcpServers", {}).get("connect_labs", {}).get("headers", {}).get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[len("Bearer ") :].strip() or None
    return None


VERTEX_DOC_TITLE = "Connect Labs Vertex Service Account"
VERTEX_VAULT = "AI-Agents"
OP_ACCOUNT = os.environ.get("CONNECT_OP_ACCOUNT") or "dimagi"
VERTEX_PROJECT_DEFAULT = "connect-labs"
VERTEX_REGION_DEFAULT = "global"
VERTEX_MODEL_DEFAULT = "claude-opus-4-7"
# Mirror of tasks.VERTEX_ALLOWED_MODELS — unknown IDs are rejected up front.
VERTEX_ALLOWED_MODELS = {
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-sonnet-4-5",
    "claude-haiku-4-5",
}


def _fetch_vertex_creds_fresh() -> Path:
    """Mirror of tasks._fetch_vertex_creds_fresh, subprocess-based."""
    if not shutil.which("op"):
        raise RuntimeError(f"1Password CLI `op` not on PATH. Install + `op signin --account {OP_ACCOUNT}`.")
    fd, tmp_str = tempfile.mkstemp(prefix="vertex.e2e.", suffix=".json")
    os.close(fd)
    tmp_path = Path(tmp_str)
    os.chmod(tmp_path, 0o600)
    cmd = [
        "op",
        "--account",
        OP_ACCOUNT,
        "document",
        "get",
        VERTEX_DOC_TITLE,
        "--vault",
        VERTEX_VAULT,
        "--out-file",
        str(tmp_path),
        "--force",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise RuntimeError(
            f"1Password fetch failed: {r.stderr.strip()[:300]} — try `op signin --account {OP_ACCOUNT}`."
        )
    try:
        data = json.loads(tmp_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise RuntimeError(f"Fetched Vertex creds unreadable: {e}")
    if data.get("type") != "service_account":
        raise RuntimeError(f"Fetched file is not a service-account key (type={data.get('type')!r})")
    return tmp_path


def _resolve_vertex(env_values: dict) -> tuple[str, str, Path, bool]:
    """Return (project, region, creds_path, is_ephemeral)."""
    project = (
        env_values.get("ANTHROPIC_VERTEX_PROJECT_ID")
        or os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID")
        or VERTEX_PROJECT_DEFAULT
    )
    region = env_values.get("CLOUD_ML_REGION") or os.environ.get("CLOUD_ML_REGION") or VERTEX_REGION_DEFAULT
    creds_rel = env_values.get("GOOGLE_APPLICATION_CREDENTIALS") or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if creds_rel:
        creds_path = Path(creds_rel)
        if not creds_path.is_absolute():
            creds_path = REPO_ROOT / creds_path
        if creds_path.exists():
            return project, region, creds_path, False
    return project, region, _fetch_vertex_creds_fresh(), True


def _run_claude(
    prompt: str,
    *,
    env: dict,
    model: str | None = None,
    timeout: int = 180,
) -> dict:
    cmd = [
        "claude",
        "--settings",
        str(SETTINGS_PATH),
        "--mcp-config",
        str(MCP_TEMPLATE_PATH),
        "--strict-mcp-config",
        "--permission-mode",
        "dontAsk",
        "--output-format",
        "json",
    ]
    if model:
        cmd += ["--model", model]
    cmd += ["-p", prompt]
    result = subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude exited {result.returncode}: {result.stderr[:500] or result.stdout[:500]}")
    for line in reversed(result.stdout.strip().splitlines()):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    raise RuntimeError(f"No JSON in claude output: {result.stdout[:500]}")


def _extract_int(text: str) -> int | None:
    m = re.search(r"-?\b(\d+)\b", text)
    return int(m.group(1)) if m else None


class _Step:
    def __init__(self, name: str) -> None:
        self.name = name
        self.passed = False
        self.detail = ""

    def ok(self, detail: str = "") -> None:
        self.passed = True
        self.detail = detail
        print(f"  PASS  {self.name}" + (f"  ({detail})" if detail else ""))

    def fail(self, detail: str) -> None:
        self.passed = False
        self.detail = detail
        print(f"  FAIL  {self.name}  ({detail})")


AUTH_MODE_VERTEX = "vertex"
AUTH_MODE_API_KEY = "api_key"

ANTHROPIC_KEY_OP_REF = "op://AI-Agents/Connect Labs Safe-Claude ZDR Anthropic API Key/password"


def _fetch_anthropic_key_fresh() -> str:
    """Mirror of tasks._fetch_anthropic_key_fresh. 1Password is the only
    source of truth — key is never read from .env or the parent shell."""
    if not shutil.which("op"):
        raise RuntimeError(f"1Password CLI `op` not on PATH. Install + `op signin --account {OP_ACCOUNT}`.")
    r = subprocess.run(
        ["op", "--account", OP_ACCOUNT, "read", ANTHROPIC_KEY_OP_REF],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"1Password fetch failed: {r.stderr.strip()[:200]} — try `op signin --account {OP_ACCOUNT}`."
        )
    key = r.stdout.strip()
    if not key.startswith("sk-ant-"):
        # Don't echo any part of `key` — leaks credential material if the
        # 1Password item is misconfigured and returned some other secret.
        raise RuntimeError("Fetched value does not look like an Anthropic API key (wrong prefix)")
    return key


def _configure_api_key_auth() -> tuple[dict, str]:
    key = _fetch_anthropic_key_fresh()
    return {"ANTHROPIC_API_KEY": key}, "Anthropic API key (from 1Password)"


def run(
    workflow_id: int,
    opportunity_id: int,
    auth: str,
    pipeline_id: int | None = None,
) -> int:
    auth_mode = auth.replace("-", "_").lower()
    if auth_mode not in (AUTH_MODE_API_KEY, AUTH_MODE_VERTEX):
        print(
            f"ERROR: Unknown --auth={auth!r}. Use 'api-key' or 'vertex'.",
            file=sys.stderr,
        )
        return 2

    env_values = _load_env(REPO_ROOT / ".env")
    pat = os.environ.get("LABS_MCP_TOKEN") or _read_user_mcp_pat()
    if not pat:
        print(
            "ERROR: connect_labs PAT missing. Run the `/labs-token-setup` skill in Claude Code.",
            file=sys.stderr,
        )
        return 2
    if not shutil.which("claude"):
        print("ERROR: `claude` CLI not on PATH.", file=sys.stderr)
        return 2
    if not SETTINGS_PATH.exists() or not MCP_TEMPLATE_PATH.exists():
        print("ERROR: Safe-mode config files missing from safe-claude/.", file=sys.stderr)
        return 2

    creds_path: Path | None = None
    creds_ephemeral = False
    try:
        if auth_mode == AUTH_MODE_API_KEY:
            auth_overrides, auth_desc = _configure_api_key_auth()
        elif auth_mode == AUTH_MODE_VERTEX:
            project, region, creds_path, creds_ephemeral = _resolve_vertex(env_values)
            auth_overrides = {
                "CLAUDE_CODE_USE_VERTEX": "1",
                "ANTHROPIC_VERTEX_PROJECT_ID": project,
                "CLOUD_ML_REGION": region,
                "GOOGLE_APPLICATION_CREDENTIALS": str(creds_path),
            }
            auth_desc = f"Vertex ({project} / {region})"
        else:
            print(
                f"ERROR: Unknown SAFE_CLAUDE_AUTH={auth_mode!r}. " f"Use 'vertex' or 'api_key'.",
                file=sys.stderr,
            )
            return 2
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    # Strip ALL auth-related vars from parent shell, then set only what the
    # chosen mode needs. Prevents stale CLAUDE_CODE_USE_VERTEX or
    # ANTHROPIC_API_KEY from leaking across auth modes.
    env = os.environ.copy()
    for k in (
        "ANTHROPIC_API_KEY",
        # Higher precedence than ANTHROPIC_API_KEY — must strip to prevent
        # a parent-shell claude.ai token from overriding the ZDR key and
        # silently routing e2e prompts through a non-governed endpoint.
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        # A proxy URL here would intercept all e2e traffic, defeating the
        # "verify ZDR routing" purpose of the smoke test.
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_USE_VERTEX",
        "ANTHROPIC_VERTEX_PROJECT_ID",
        "CLOUD_ML_REGION",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ):
        env.pop(k, None)
    env.update(auth_overrides)
    # Consumed by Claude Code's ${LABS_MCP_TOKEN} expansion in the
    # checked-in mcp.safe.json — no MCP-config tempfile needed.
    env["LABS_MCP_TOKEN"] = pat

    # Force a specific model in Vertex mode (regional availability varies).
    # In api_key mode, let Claude Code pick its Anthropic-API default unless
    # the operator explicitly overrides via SAFE_CLAUDE_MODEL.
    model_override = os.environ.get("SAFE_CLAUDE_MODEL")
    if auth_mode == AUTH_MODE_VERTEX:
        model = model_override or VERTEX_MODEL_DEFAULT
        if model not in VERTEX_ALLOWED_MODELS:
            print(
                f"ERROR: Model {model!r} is not in the Vertex allowlist. "
                f"Allowed: {sorted(VERTEX_ALLOWED_MODELS)}.",
                file=sys.stderr,
            )
            return 2
    else:
        model = model_override  # None → Claude Code default

    # Include PID alongside the timestamp so two concurrent e2e runs
    # against the same workflow can't stomp each other's markers.
    marker = f"e2e-test-{int(time.time())}-{os.getpid()}"

    print("=== safe-claude end-to-end smoke test ===")
    print(f"auth: {auth_desc}")
    print(f"model: {model or '(Claude Code default)'}")
    print(f"workflow_id={workflow_id}  opportunity_id={opportunity_id}" f"  pipeline_id={pipeline_id}")
    print(f"marker={marker}")
    print()

    failed: list[str] = []

    try:
        # Step 1 — read initial version
        s = _Step("workflow_get → initial version")
        r = _run_claude(
            f"Call the workflow_get tool with workflow_id={workflow_id} and "
            f"opportunity_id={opportunity_id}. Respond with ONLY the integer "
            f"render_code_version. No words, no quotes, no punctuation.",
            env=env,
            model=model,
        )
        initial_version = _extract_int(r.get("result", ""))
        if initial_version is None:
            s.fail(f"unparseable: {r.get('result','')[:160]!r}")
            failed.append(s.name)
            return 1
        s.ok(f"v={initial_version}")

        # Step 2 — update with marker
        s = _Step("workflow_update_render_code → append marker")
        r = _run_claude(
            f"Do these steps exactly:\n"
            f"1. Call workflow_get with workflow_id={workflow_id} and "
            f"opportunity_id={opportunity_id}.\n"
            f"2. Take the returned render_code string and append this line "
            f"at the very end on its own line: `/* {marker} */`.\n"
            f"3. Call workflow_update_render_code with workflow_id="
            f"{workflow_id}, opportunity_id={opportunity_id}, component_code "
            f"= the modified string, expected_version={initial_version}.\n"
            f"4. Respond with ONLY the new integer version number returned "
            f"by the update. No words.",
            env=env,
            model=model,
        )
        new_version = _extract_int(r.get("result", ""))
        if new_version is None or new_version <= initial_version:
            s.fail(f"expected v>{initial_version}, got {r.get('result','')[:160]!r}")
            failed.append(s.name)
            return 1
        s.ok(f"v={initial_version} → v={new_version}")

        # Step 3 — verify marker landed
        s = _Step("workflow_get → marker present")
        r = _run_claude(
            f"Call workflow_get with workflow_id={workflow_id} and "
            f"opportunity_id={opportunity_id}. Does the render_code string "
            f"contain the exact substring `{marker}`? Respond with ONLY "
            f"'YES' or 'NO'. Nothing else.",
            env=env,
            model=model,
        )
        answer = r.get("result", "").strip().upper()
        if "YES" not in answer:
            s.fail(f"marker absent, got {answer[:160]!r}")
            failed.append(s.name)
            return 1
        s.ok("marker found in pushed JSX")

        # Step 4 — revert
        s = _Step("workflow_update_render_code → revert marker")
        r = _run_claude(
            f"Do these steps exactly:\n"
            f"1. Call workflow_get with workflow_id={workflow_id} and "
            f"opportunity_id={opportunity_id}.\n"
            f"2. Take the render_code and remove every line that contains "
            f"the substring `{marker}`.\n"
            f"3. Call workflow_update_render_code with workflow_id="
            f"{workflow_id}, opportunity_id={opportunity_id}, component_code "
            f"= the cleaned string, expected_version = the version returned "
            f"in step 1.\n"
            f"4. Respond with ONLY the new integer version number. No words.",
            env=env,
            model=model,
        )
        revert_version = _extract_int(r.get("result", ""))
        if revert_version is None or revert_version <= new_version:
            s.fail(f"expected v>{new_version}, got {r.get('result','')[:160]!r}")
            failed.append(s.name)
            return 1
        s.ok(f"v={new_version} → v={revert_version}  (JSX restored)")

        # Steps 5–6 — pipeline (optional)
        if pipeline_id is not None:
            s = _Step("pipeline_sql → returns SQL")
            r = _run_claude(
                f"Call pipeline_sql with pipeline_id={pipeline_id} and "
                f"opportunity_id={opportunity_id}. Respond with ONLY the "
                f"first 120 characters of the SQL query returned. No words "
                f"around it.",
                env=env,
                model=model,
            )
            sql_head = (r.get("result") or "").strip()
            if not re.search(r"\b(select|with)\b", sql_head, re.IGNORECASE):
                s.fail(f"no SELECT/WITH in response: {sql_head[:160]!r}")
                failed.append(s.name)
                return 1
            s.ok(f"SQL starts: {sql_head[:60]!r}")

            s = _Step("pipeline_preview → returns row count")
            r = _run_claude(
                f"Call pipeline_preview with pipeline_id={pipeline_id}, "
                f"opportunity_id={opportunity_id}, limit=5. Respond with "
                f"ONLY the integer number of rows in the preview. No words.",
                env=env,
                model=model,
            )
            rows = _extract_int(r.get("result", ""))
            if rows is None:
                s.fail(f"unparseable row count: {r.get('result','')[:160]!r}")
                failed.append(s.name)
                return 1
            s.ok(f"preview rows = {rows}")

        print()
        print("=== ALL PASSED ===")
        return 0

    except RuntimeError as e:
        print(f"  ERROR {e}", file=sys.stderr)
        return 1
    finally:
        if creds_ephemeral:
            try:
                os.unlink(creds_path)
            except OSError:
                pass


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--auth",
        required=True,
        choices=["vertex", "api-key", "api_key"],
        help="Auth mode — REQUIRED, no default. 'vertex' or 'api-key'.",
    )
    ap.add_argument("--workflow-id", type=int, required=True)
    ap.add_argument("--opportunity-id", type=int, required=True)
    ap.add_argument("--pipeline-id", type=int, default=None)
    args = ap.parse_args()
    sys.exit(run(args.workflow_id, args.opportunity_id, args.auth, args.pipeline_id))


if __name__ == "__main__":
    main()
