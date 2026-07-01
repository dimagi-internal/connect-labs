"""Useful tasks for use when developing CommCare Connect.

This uses the `Invoke` library."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from invoke import Context, Exit, call, task

PROJECT_DIR = Path(__file__).parent


@task
def docker(c: Context, command):
    """Run docker compose"""
    if command == "up":
        c.run("docker compose -f docker-compose.yml up -d")
    elif command == "down":
        c.run("docker compose -f docker-compose.yml down")
    else:
        raise Exit(f"Unknown docker command: {command}", -1)


@task(pre=[call(docker, command="up")])
def up(c: Context):
    """Run docker compose [up]"""
    pass


@task(pre=[call(docker, command="down")])
def down(c: Context):
    """Run docker compose [down]"""
    pass


@task
def requirements(c: Context, upgrade=False, upgrade_package=None):
    if upgrade and upgrade_package:
        raise Exit("Cannot specify both upgrade and upgrade-package", -1)
    args = " -U" if upgrade else ""
    cmd_base = "pip-compile -q --resolver=backtracking"
    env = {"CUSTOM_COMPILE_COMMAND": "inv requirements"}
    if upgrade_package:
        cmd_base += f" --upgrade-package {upgrade_package}"
    c.run(f"{cmd_base} requirements/base.in{args}", env=env)
    c.run(f"{cmd_base} requirements/dev.in{args}", env=env)


@task
def translations(c: Context):
    """Make Django translations"""
    c.run("python manage.py makemessages --all --ignore node_modules --ignore venv")
    c.run("python manage.py makemessages -d djangojs --all --ignore node_modules --ignore venv")
    c.run("python manage.py compilemessages")


@task(
    help={
        "workflow_id": "Workflow definition labs-record id (e.g. 2787 for MBW Monitoring V3)",
        "file": "Path to the render-code .js file",
        "opportunity_id": "Opportunity id used for the OAuth scope check (default: 765)",
    }
)
def push_render(
    c: Context,
    workflow_id: int,
    file: str,
    opportunity_id: int = 765,
):
    """Push a local render-code .js file to a workflow's labs_record.

    Both local and prod read render code from the workflow's stored
    `workflow_render_code` LabsRecord — set at workflow-creation time from
    the template's render_code, frozen thereafter. Edits to the template's
    .js file in the source tree don't affect a workflow that already
    exists; they only affect newly-created workflows. Use this task to
    push the latest .js content into an existing workflow's record so the
    next page reload picks it up.

    Reads the file from disk and POSTs it via the same labs_record API
    the connect_labs MCP `workflow_update_render_code` tool uses. The
    local task path is preferred for large templates (~380KB) because
    round-tripping the content through Claude's context is expensive
    (~100k tokens). For small templates the MCP tool is fine.

    Usage:
        inv push-render --workflow-id=2787 \\
            --file=connect_labs/workflow/templates/mbw_auditing_v5_render.js
    """
    import os

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    import django

    django.setup()

    from connect_labs.labs.integrations.connect.cli import create_cli_request
    from connect_labs.workflow.data_access import WorkflowDataAccess

    request = create_cli_request(opportunity_id=opportunity_id)
    if not request:
        raise Exit(
            "Failed to create CLI request — run `python manage.py get_cli_token` first.",
            -1,
        )
    file_path = Path(file)
    if not file_path.exists():
        raise Exit(f"Render file not found: {file_path}", -1)

    code = file_path.read_text()
    print(f"Pushing {len(code):,} bytes from {file_path} to workflow {workflow_id}...")

    da = WorkflowDataAccess(request=request)
    result = da.save_render_code(definition_id=workflow_id, component_code=code)
    print(f"OK: render_code id={result.id}, version={result.data.get('version')}")
    print("Reload the deployed workflow page to pick up the new code.")


@task
def build_js(c: Context, watch=False, prod=False):
    """Build the JavaScript and CSS assets"""
    if prod:
        if watch:
            print("[warn] Prod build can't be watched")
        c.run("npm run build")
    else:
        extra = "-watch" if watch else ""
        c.run(f"npm run dev{extra}")


@task
def setup_ec2(c: Context, env="staging", verbose=False, diff=False):
    run_ansible(c, env=env, verbose=verbose, diff=diff)

    kamal_cmd = f"kamal env push -d {env}"
    if verbose:
        kamal_cmd += " -v"
    with c.cd(PROJECT_DIR / "deploy"):
        c.run(kamal_cmd)


@task
def django_settings(c: Context, env="staging", verbose=False, diff=False):
    """Update the Django settings file on prod servers"""
    run_ansible(c, env=env, tags="django_settings", verbose=verbose, diff=diff, user="connect", become=False)
    print("\nSettings updated. A re-deploy is required to have the services use the new settings.")
    val = input("Do you want to re-deploy the Django services? [y/N] ")
    if val.lower() == "y":
        deploy(c, env=env)


@task
def restart_django(c: Context, env="staging", verbose=False, diff=False):
    """Restart the Django server on prod servers"""
    run_ansible(c, play="utils.yml", env=env, tags="restart", verbose=verbose, diff=diff)


@task
def run_ansible(
    c: Context, play="play.yml", env="staging", tags=None, verbose=False, diff=False, user="ubuntu", become=True
):
    ansible_cmd = f"ansible-playbook {play} -i {env}.inventory.yml"
    if tags:
        ansible_cmd += f" --tags {tags}"
    if verbose:
        ansible_cmd += " -v"
    if diff:
        ansible_cmd += " -D"
    if user:
        ansible_cmd += f" -u {user}"
    if become:
        ansible_cmd += " -b"

    with c.cd(PROJECT_DIR / "deploy"):
        c.run(ansible_cmd)


@task
def deploy(c: Context, env="staging"):
    """Deploy the app to prod servers"""
    with c.cd(PROJECT_DIR / "deploy"):
        c.run(f"kamal deploy -d {env}")


@task
def check(c: Context):
    """Validate the development environment before starting the backend"""
    c.run(f"bash {PROJECT_DIR / 'tools' / 'check_dev_environment.sh'}", warn=True)


def _load_env_file(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _read_labs_pat_from_user_mcp() -> str | None:
    """Extract the connect_labs bearer token from Claude Code's user-scope
    MCP config, populated by `claude mcp add --scope user` (which the
    `/labs-token-setup` skill runs).

    Claude Code persists user-scope MCP servers inside ~/.claude.json under
    the top-level `mcpServers` key — not in a separate ~/.claude/mcp.json.
    We check both for resilience to future config-layout changes."""
    for path in (Path.home() / ".claude.json", Path.home() / ".claude" / "mcp.json"):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        auth = data.get("mcpServers", {}).get("connect_labs", {}).get("headers", {}).get("Authorization", "")
        prefix = "Bearer "
        if auth.startswith(prefix):
            return auth[len(prefix) :].strip() or None
    return None


VERTEX_DOC_TITLE = "Connect Labs Vertex Service Account"
VERTEX_VAULT = "AI-Agents"
# 1Password account name — overridable for teams that rename the account or
# run against a different vault (CI, alternate workspaces).
OP_ACCOUNT = os.environ.get("CONNECT_OP_ACCOUNT") or "dimagi"
# Static Vertex config — hard-coded so safe mode never depends on a
# human-editable .env for the auth endpoint. Overridable via env values
# (from .env or parent shell) for testing; GCP project/region are stable
# infrastructure, not secrets.
VERTEX_PROJECT_DEFAULT = "connect-labs"
# `global` is Vertex's region-agnostic endpoint for newer Claude models
# (4.6, 4.7, etc.) — they are GA-listed in regional catalogs but only
# actually servable via global. Regional endpoints (us-east5, us-central1)
# still work for older tiers like sonnet-4-5 but return "not servable" for
# the latest models on this project.
VERTEX_REGION_DEFAULT = "global"
# Models we've enabled in Vertex AI Model Garden on `connect-labs` and
# confirmed serve from the `global` endpoint. Any override via
# SAFE_CLAUDE_MODEL must appear here; unknown IDs are rejected up front
# instead of after the first prompt (which would have sent PII to Vertex
# before Google's 404 came back).
VERTEX_ALLOWED_MODELS = {
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-sonnet-4-5",
    "claude-haiku-4-5",
}
VERTEX_MODEL_DEFAULT = "claude-opus-4-7"


def _validate_service_account_file(path: Path) -> None:
    """Raise Exit unless `path` is a readable Google service-account JSON."""
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise Exit(f"Vertex credentials at {path} unreadable: {e}", -1)
    if data.get("type") != "service_account":
        raise Exit(
            f"Vertex credentials at {path} are not a service account " f"(type={data.get('type')!r}).",
            -1,
        )


def _fetch_vertex_creds_fresh(c: Context) -> Path:
    """Fetch the Vertex service-account JSON from 1Password into a 0600
    tempfile. Caller is responsible for deleting it on exit.

    Requires `op` CLI signed in to the dimagi account.
    """
    if not shutil.which("op"):
        raise Exit(
            "1Password CLI `op` not found on PATH. Install it, sign in with "
            "`op signin --account dimagi`, and retry. Or run `inv vertex-setup` "
            "to persist creds to .gcp/vertex.json once.",
            -1,
        )

    fd, tmp_str = tempfile.mkstemp(prefix="vertex.", suffix=".json")
    os.close(fd)
    tmp_path = Path(tmp_str)
    os.chmod(tmp_path, 0o600)
    cmd = (
        f'op --account {OP_ACCOUNT} document get "{VERTEX_DOC_TITLE}" '
        f'--vault "{VERTEX_VAULT}" --out-file "{tmp_path}" --force'
    )
    result = c.run(cmd, warn=True, hide=True)
    if result.exited != 0:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise Exit(
            f"1Password fetch failed. Sign in with `op signin --account dimagi` "
            f"and retry. (op stderr: {result.stderr.strip()[:200]})",
            result.exited,
        )
    _validate_service_account_file(tmp_path)
    return tmp_path


def _resolve_vertex(c: Context, env_values: dict) -> tuple[str, str, Path, bool]:
    """Decide where to source the Vertex service-account JSON from.

    Resolution order:
      1. Honor GOOGLE_APPLICATION_CREDENTIALS if it's set in `.env` or the
         parent shell AND the file actually exists (offline / cached path).
      2. Otherwise, fetch fresh from 1Password into a 0600 tempfile.

    Returns (project, region, credentials_path, is_ephemeral). The caller
    must unlink the path when is_ephemeral is True.
    """
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
            creds_path = PROJECT_DIR / creds_path
        if creds_path.exists():
            _validate_service_account_file(creds_path)
            return project, region, creds_path, False
        # Configured path is stale; fall through to a fresh fetch.

    tmp = _fetch_vertex_creds_fresh(c)
    return project, region, tmp, True


@task
def vertex_setup(c: Context):
    """Fetch the Connect Labs Vertex service-account JSON from 1Password and
    write it to .gcp/vertex.json (mode 0600). Run once per machine, then
    `inv safe-claude` works.

    Prereqs: 1Password CLI (`op`) signed in to the dimagi account.
    """
    if not shutil.which("op"):
        raise Exit("1Password CLI `op` not found on PATH.", -1)

    gcp_dir = PROJECT_DIR / ".gcp"
    gcp_dir.mkdir(exist_ok=True)
    target = gcp_dir / "vertex.json"

    # op writes the document directly to disk — the secret never transits
    # through shell stdout (no scrollback / process listing leakage).
    cmd = (
        f'op --account {OP_ACCOUNT} document get "{VERTEX_DOC_TITLE}" '
        f'--vault AI-Agents --out-file "{target}" --force'
    )
    result = c.run(cmd, warn=True)
    if result.exited != 0:
        raise Exit(
            f"1Password fetch failed (code {result.exited}). Are you signed "
            "in to the dimagi account? Try `op signin --account dimagi`.",
            result.exited,
        )
    os.chmod(target, 0o600)

    try:
        data = json.loads(target.read_text())
    except json.JSONDecodeError as e:
        raise Exit(f"Fetched file is not valid JSON: {e}", -1)
    if data.get("type") != "service_account":
        raise Exit(
            f"Fetched file is not a service-account key " f"(type={data.get('type')!r}).",
            -1,
        )

    print(f"Wrote {target} (mode 0600)")
    print(f"  project_id   = {data.get('project_id')}")
    print(f"  client_email = {data.get('client_email')}")
    print("Ready to run `inv safe-claude`.")


AUTH_MODE_VERTEX = "vertex"
AUTH_MODE_API_KEY = "api_key"

ANTHROPIC_KEY_OP_REF = "op://AI-Agents/Connect Labs Safe-Claude ZDR Anthropic API Key/password"


def _fetch_anthropic_key_fresh(c: Context) -> str:
    """Fetch the Anthropic ZDR API key from 1Password at launch time.

    The key is NEVER read from .env or the parent shell — the 1Password vault
    is the single source of truth. Rotating the key in 1Password propagates
    to the next `inv safe-claude` launch with no local cleanup needed.
    """
    if not shutil.which("op"):
        raise Exit(
            "1Password CLI `op` not on PATH. Install it and run " "`op signin --account dimagi`, then retry.",
            -1,
        )
    result = c.run(
        f'op --account {OP_ACCOUNT} read "{ANTHROPIC_KEY_OP_REF}"',
        warn=True,
        hide=True,
    )
    if result.exited != 0:
        raise Exit(
            f"1Password fetch failed. Sign in with `op signin --account dimagi` "
            f"and retry. (stderr: {result.stderr.strip()[:200]})",
            result.exited,
        )
    key = result.stdout.strip()
    if not key.startswith("sk-ant-"):
        # Deliberately do NOT echo any part of `key` — if 1Password is
        # misconfigured and returned some other secret, logging even a
        # prefix leaks credential material into stderr/scrollback.
        raise Exit(
            "Fetched value does not look like an Anthropic API key " "(wrong prefix). Check the 1Password item.",
            -1,
        )
    return key


def _configure_api_key_auth(c: Context) -> tuple[dict, str]:
    """Env overrides for the Anthropic API-key auth path.

    Returns (env_overrides, human_description). Caller merges overrides
    into the child subprocess env AFTER stripping any inherited auth vars.
    """
    key = _fetch_anthropic_key_fresh(c)
    return {"ANTHROPIC_API_KEY": key}, "Anthropic API key (from 1Password)"


def _configure_vertex_auth(c: Context, env_values: dict) -> tuple[dict, Path | None, str]:
    """Env overrides for the Vertex auth path.

    Returns (env_overrides, ephemeral_creds_path_or_None, human_description).
    If ephemeral_creds_path is not None, the caller MUST unlink it on exit.
    """
    project, region, creds_path, creds_ephemeral = _resolve_vertex(c, env_values)
    model = os.environ.get("SAFE_CLAUDE_MODEL") or VERTEX_MODEL_DEFAULT
    overrides = {
        "CLAUDE_CODE_USE_VERTEX": "1",
        "ANTHROPIC_VERTEX_PROJECT_ID": project,
        "CLOUD_ML_REGION": region,
        "GOOGLE_APPLICATION_CREDENTIALS": str(creds_path),
    }
    desc = f"Vertex ({project} / {region}, model: {model})"
    return overrides, (creds_path if creds_ephemeral else None), desc


def _resolve_auth_mode(auth: str | None) -> str:
    """Validate --auth and return the canonical mode string. No default —
    the operator MUST pick explicitly per run so there's no ambient 'which
    endpoint am I routing PII through right now?' ambiguity."""
    if auth is None:
        raise Exit(
            "--auth is required. Pick one explicitly each run:\n"
            "  inv safe-claude --auth=api-key   (Anthropic ZDR key, from 1Password)\n"
            "  inv safe-claude --auth=vertex    (Google Vertex AI, GCP-governed)\n"
            "\nSee docs/SAFE_MODE.md for how each mode routes traffic.",
            -1,
        )
    mode = auth.replace("-", "_").lower()
    if mode not in (AUTH_MODE_API_KEY, AUTH_MODE_VERTEX):
        raise Exit(
            f"Unknown --auth={auth!r}. Use 'api-key' or 'vertex'.",
            -1,
        )
    return mode


@task(
    help={
        "auth": "REQUIRED. Auth mode — 'vertex' or 'api-key'. No default: "
        "you must pick explicitly every run so it's always obvious which "
        "governed endpoint your PII is routing through.",
    }
)
def safe_claude(c: Context, auth=None):
    """Launch Claude Code in PII-safe mode against the labs MCP servers.

    Usage:
        inv safe-claude --auth=api-key   # Anthropic ZDR key (from 1Password)
        inv safe-claude --auth=vertex    # Google Vertex AI

    Both modes fetch their secret from 1Password at launch — nothing
    persists on disk. ANTHROPIC_API_KEY from the parent shell is stripped
    before we set our own, so there's no fallback to a non-governed
    endpoint either way.

    LABS_MCP_TOKEN is read from ~/.claude.json (via the `/labs-token-setup`
    skill) and injected via env-var expansion in `safe-claude/mcp.json`, so
    the PAT never persists on disk.

    Locks the session to `connect_labs` + `commcare_hq_mcp` only, with
    Write/Edit/Bash/WebFetch/WebSearch/Agent/Cron*/ScheduleWakeup denied.
    See docs/SAFE_MODE.md.
    """
    auth_mode = _resolve_auth_mode(auth)

    claude_bin = shutil.which("claude")
    if not claude_bin:
        raise Exit("`claude` CLI not found on PATH. Install Claude Code first.", -1)

    mcp_token = os.environ.get("LABS_MCP_TOKEN") or _read_labs_pat_from_user_mcp()
    if not mcp_token:
        raise Exit(
            "No connect_labs PAT found. Run the `/labs-token-setup` skill in "
            "Claude Code to register one (writes to ~/.claude.json), then "
            "rerun `inv safe-claude`.",
            -1,
        )

    settings_path = PROJECT_DIR / "safe-claude" / "settings.json"
    mcp_config_path = PROJECT_DIR / "safe-claude" / "mcp.json"
    if not settings_path.exists() or not mcp_config_path.exists():
        raise Exit("Safe-mode config files missing from safe-claude/.", -1)

    # .env is consulted only for optional Vertex overrides (cached creds
    # path, alt project/region for testing). Not required for either mode.
    env_values = _load_env_file(PROJECT_DIR / ".env")

    ephemeral_path: Path | None = None
    isolated_config_dir: Path | None = None
    try:
        if auth_mode == AUTH_MODE_API_KEY:
            auth_overrides, auth_desc = _configure_api_key_auth(c)
            # Point the subprocess at an empty config dir so it can't see a
            # persisted claude.ai OAuth session (~/.claude/.credentials.json
            # or macOS Keychain). Without this, users already logged into
            # claude.ai see "Auth conflict: Both a token (claude.ai) and an
            # API key (ANTHROPIC_API_KEY) are set" at launch. The API key
            # wins by precedence so traffic still routes through the ZDR
            # endpoint, but the warning is confusing and leaves uncertainty
            # about which credential is actually in use. Scoped to this
            # subprocess only — the user's real ~/.claude session is
            # untouched. Vertex mode doesn't set ANTHROPIC_API_KEY, so the
            # warning doesn't fire there and the redirect isn't needed.
            isolated_config_dir = Path(tempfile.mkdtemp(prefix="safe-claude-cfg-"))
        else:  # AUTH_MODE_VERTEX
            auth_overrides, ephemeral_path, auth_desc = _configure_vertex_auth(c, env_values)

        # Build child env: strip ALL auth-related vars first, then apply only
        # what the chosen mode needs. This prevents stale CLAUDE_CODE_USE_VERTEX
        # or ANTHROPIC_API_KEY from the parent shell leaking across auth modes.
        env = os.environ.copy()
        for k in (
            "ANTHROPIC_API_KEY",
            # Higher precedence than ANTHROPIC_API_KEY in Claude Code's auth
            # resolution — if either leaks in from the parent shell it
            # overrides the 1Password-sourced key we set below, silently
            # routing PII through whatever token the user's broader
            # environment carries instead of the chosen governed endpoint.
            "ANTHROPIC_AUTH_TOKEN",
            "CLAUDE_CODE_OAUTH_TOKEN",
            # Redirects the Anthropic API endpoint. A leaked value from the
            # parent shell (e.g. pointing at a local proxy or LiteLLM gateway)
            # would route ZDR-intended traffic through an unaudited host,
            # defeating the "I know which governed endpoint my PII is going
            # to" guarantee safe-mode is built around.
            "ANTHROPIC_BASE_URL",
            "CLAUDE_CODE_USE_VERTEX",
            "ANTHROPIC_VERTEX_PROJECT_ID",
            "CLOUD_ML_REGION",
            "GOOGLE_APPLICATION_CREDENTIALS",
        ):
            env.pop(k, None)
        env.update(auth_overrides)
        # Consumed by Claude Code's ${LABS_MCP_TOKEN} expansion in
        # safe-claude/mcp.json — the token never lands on disk.
        env["LABS_MCP_TOKEN"] = mcp_token
        if isolated_config_dir is not None:
            env["CLAUDE_CONFIG_DIR"] = str(isolated_config_dir)

        cmd_argv = [
            claude_bin,
            "--settings",
            str(settings_path),
            "--mcp-config",
            str(mcp_config_path),
            "--strict-mcp-config",
            "--permission-mode",
            "dontAsk",
        ]
        # Force a specific model only in Vertex mode — Vertex needs an ID
        # we've enabled in Model Garden (regional availability varies). In
        # api_key mode let Claude Code use its Anthropic-API default unless
        # the operator explicitly overrides via SAFE_CLAUDE_MODEL.
        model_override = os.environ.get("SAFE_CLAUDE_MODEL")
        if auth_mode == AUTH_MODE_VERTEX:
            model = model_override or VERTEX_MODEL_DEFAULT
            # Reject unknown Vertex model IDs up front so the first prompt
            # (which may contain PII) never goes out against a model that
            # will 404 and waste the round-trip.
            if model not in VERTEX_ALLOWED_MODELS:
                raise Exit(
                    f"Model {model!r} is not in the Vertex allowlist for this project. "
                    f"Allowed: {sorted(VERTEX_ALLOWED_MODELS)}. "
                    "Enable the model in Vertex AI Model Garden and add it to "
                    "VERTEX_ALLOWED_MODELS in tasks.py before using it.",
                    -1,
                )
            cmd_argv += ["--model", model]
        elif model_override:
            # api_key mode passes any SAFE_CLAUDE_MODEL override through to
            # Claude Code unvalidated — the Anthropic API has a different
            # set of accepted IDs and we don't police it from here.
            # ZDR is enforced at the key level (the key is scoped to the ZDR
            # workspace), not the model level, so any model accepted by that
            # key routes through the same governed endpoint.
            cmd_argv += ["--model", model_override]

        print(f"Launching Claude Code in safe mode — auth: {auth_desc}")
        print("Ctrl-D or /exit to quit.")
        # subprocess.run (not invoke's c.run) so Claude Code's TUI inherits
        # the parent shell's real TTY directly — no PTY allocation, no
        # terminal-state corruption on exit, and keys like Enter reach the
        # child's input handler normally.
        result = subprocess.run(cmd_argv, env=env)
        if result.returncode != 0:
            raise Exit(f"Claude Code exited with code {result.returncode}", result.returncode)
    finally:
        if ephemeral_path is not None:
            try:
                os.unlink(ephemeral_path)
            except OSError:
                pass
        if isolated_config_dir is not None:
            try:
                shutil.rmtree(isolated_config_dir)
            except OSError as e:
                # Don't raise — this runs in finally, and masking the real
                # exit code over a cleanup glitch hides the actual failure.
                # Do surface it: the subprocess may have written cached
                # state into this dir (prompts, tool transcripts) that
                # shouldn't linger.
                print(
                    "\n"
                    "=" * 70 + "\n"
                    "WARNING: session state cleanup failed\n"
                    f"  path : {isolated_config_dir}\n"
                    f"  error: {e}\n"
                    "Claude Code may have written conversation transcripts (possibly\n"
                    "containing PII) into that directory. Delete it manually:\n"
                    f"  rm -rf {isolated_config_dir}\n"
                    "=" * 70,
                    file=sys.stderr,
                )


@task(
    help={
        "workflow_id": "Workflow ID to round-trip (required)",
        "opportunity_id": "Opportunity that owns the workflow and pipeline (required)",
        "pipeline_id": "Optional pipeline ID to exercise pipeline_sql + pipeline_preview",
        "auth": "REQUIRED. Auth mode — 'vertex' or 'api-key'. Same semantics as `inv safe-claude`.",
    }
)
def safe_claude_e2e(c: Context, workflow_id, opportunity_id, auth=None, pipeline_id=None):
    """End-to-end smoke test for safe-claude.

    Drives `claude -p` through the same safe-mode config as `inv safe-claude`
    and verifies a full workflow render-code round-trip (read → append marker
    → push → verify → revert) plus optional pipeline SQL/preview reads
    against a live labs workflow. Pick a DISPOSABLE workflow you control.
    See docs/SAFE_MODE.md.

    Usage:
        inv safe-claude-e2e --auth=api-key --workflow-id=2578 --opportunity-id=1237
        inv safe-claude-e2e --auth=vertex  --workflow-id=2578 --opportunity-id=1237 --pipeline-id=2577
    """
    auth_mode = _resolve_auth_mode(auth)
    script = PROJECT_DIR / "safe-claude" / "e2e.py"
    cmd = (
        f'python "{script}" '
        f"--auth {auth_mode.replace('_', '-')} "
        f"--workflow-id {int(workflow_id)} "
        f"--opportunity-id {int(opportunity_id)}"
    )
    if pipeline_id is not None:
        cmd += f" --pipeline-id {int(pipeline_id)}"
    result = c.run(cmd, warn=True)
    if result.exited != 0:
        raise Exit("safe-claude e2e failed", result.exited)
