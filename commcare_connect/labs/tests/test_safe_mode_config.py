"""Regression tests for the `inv safe-claude` lockdown config.

These do NOT test Claude Code's enforcement — Anthropic does that. They pin
the invariants of our `safe-claude/settings.json` and `safe-claude/mcp.json`
so config drift (e.g., someone re-adding `Bash` to `allow`) fails CI.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SETTINGS_PATH = REPO_ROOT / "safe-claude" / "settings.json"
MCP_PATH = REPO_ROOT / "safe-claude" / "mcp.json"


@pytest.fixture(scope="module")
def safe_settings() -> dict:
    return json.loads(SETTINGS_PATH.read_text())


@pytest.fixture(scope="module")
def mcp_config() -> dict:
    return json.loads(MCP_PATH.read_text())


def test_safe_mode_config_files_exist():
    assert SETTINGS_PATH.exists()
    assert MCP_PATH.exists()


def test_safe_mode_denies_filesystem_shell_network_and_subagents(safe_settings):
    deny = set(safe_settings["permissions"]["deny"])
    required_denies = {
        "Write",
        "Edit",
        "NotebookEdit",
        "Bash",
        "WebFetch",
        "WebSearch",
        "Agent",
        "CronCreate",
        "CronDelete",
        "CronList",
        "ScheduleWakeup",
        # RemoteTrigger: same escape risk as CronCreate — a triggered background
        # agent runs with the user's default config, not safe-mode config.
        "RemoteTrigger",
    }
    missing = required_denies - deny
    assert not missing, f"Safe mode must deny: {sorted(missing)}"


def test_safe_mode_denies_sensitive_file_reads(safe_settings):
    """Path-scoped Read/Grep denies block prompt-injection exfiltration via
    read-then-write-to-MCP. Grep is included because it also returns file
    contents and is not covered by Read deny rules."""
    deny = safe_settings["permissions"]["deny"]
    required_path_denies = {
        "Read(./.env)",
        "Read(./.env.*)",
        "Read(./.gcp/**)",
        "Read(~/.claude.json)",
        "Read(~/.claude/**)",
        "Grep(./.env)",
        "Grep(./.env.*)",
        # Connect CLI OAuth token — exfiltration target distinct from .env
        "Read(~/.commcare-connect/**)",
        "Glob(~/.commcare-connect/**)",
        "Grep(~/.commcare-connect/**)",
    }
    missing = required_path_denies - set(deny)
    assert not missing, f"Safe mode must include path-scoped denies: {sorted(missing)}"


def test_safe_mode_does_not_allow_dangerous_tools(safe_settings):
    allow = set(safe_settings["permissions"]["allow"])
    forbidden_in_allow = {
        "Write",
        "Edit",
        "NotebookEdit",
        "Bash",
        "WebFetch",
        "WebSearch",
        "Agent",
        "RemoteTrigger",
    }
    leaked = allow & forbidden_in_allow
    assert not leaked, f"These tools must NOT appear in safe-mode allow list: {sorted(leaked)}"


def test_safe_mode_uses_dontask_permission_mode(safe_settings):
    mode = safe_settings["permissions"].get("defaultMode")
    assert mode == "dontAsk", (
        f"Safe mode must use defaultMode=dontAsk so the user cannot interactively "
        f"approve denied or unknown tools, got {mode!r}"
    )


def test_safe_mode_disables_escape_hatches(safe_settings):
    perms = safe_settings["permissions"]
    assert perms.get("disableBypassPermissionsMode") == "disable", (
        "Safe mode must set disableBypassPermissionsMode=disable to block " "--dangerously-skip-permissions escape"
    )
    assert perms.get("disableAutoMode") == "disable", (
        "Safe mode must set disableAutoMode=disable so the AI classifier "
        "cannot auto-approve calls we did not allow-list"
    )


def test_safe_mode_allows_expected_surface(safe_settings):
    allow = set(safe_settings["permissions"]["allow"])
    required = {
        "mcp__connect_labs__*",
        "mcp__commcare_hq_mcp__*",
        "Read",
        "Grep",
        "Glob",
    }
    missing = required - allow
    assert not missing, f"Safe mode allow list is missing: {sorted(missing)}"


def test_safe_mode_telemetry_disabled(safe_settings):
    env = safe_settings.get("env", {})
    assert env.get("DISABLE_TELEMETRY") == "1"
    assert env.get("DISABLE_ERROR_REPORTING") == "1"


def test_safe_mode_mcp_surface_is_exactly_expected_servers(mcp_config):
    servers = set(mcp_config["mcpServers"])
    assert servers == {"connect_labs", "commcare_hq_mcp"}, f"Safe-mode MCP surface drifted: {sorted(servers)}"


def test_safe_mode_connect_labs_points_at_labs_not_localhost(mcp_config):
    url = mcp_config["mcpServers"]["connect_labs"]["url"]
    assert url.startswith("https://labs.connect.dimagi.com/"), f"connect_labs URL should be the labs host, got {url!r}"


def test_safe_mode_mcp_template_has_no_real_token(mcp_config):
    """The checked-in file is a template — the PAT is injected at launch time.
    If a real token is committed here, fail hard."""
    auth = mcp_config["mcpServers"]["connect_labs"]["headers"]["Authorization"]
    assert (
        auth == "Bearer ${LABS_MCP_TOKEN}"
    ), f"Safe-mode MCP config must keep the ${{LABS_MCP_TOKEN}} placeholder, got {auth!r}"


def test_safe_mode_env_tpl_does_not_source_anthropic_key_locally():
    """The Anthropic ZDR API key must be fetched from 1Password at launch,
    not baked into .env via op-inject. Any `SAFE_CLAUDE_ANTHROPIC_API_KEY=`
    or raw `sk-ant-` literal in .env.tpl means a key ends up on disk in the
    rendered .env — which is exactly what 'single source of truth in 1Password'
    is supposed to prevent. See `_fetch_anthropic_key_fresh` in tasks.py."""
    tpl = (REPO_ROOT / ".env.tpl").read_text()
    assert "SAFE_CLAUDE_ANTHROPIC_API_KEY" not in tpl, (
        ".env.tpl must not define SAFE_CLAUDE_ANTHROPIC_API_KEY — the Anthropic "
        "ZDR API key is fetched from 1Password by `inv safe-claude` on every "
        "launch and must never be rendered into .env."
    )
    assert "sk-ant-" not in tpl, (
        ".env.tpl contains a literal Anthropic API key — rotate immediately " "and remove it from the template."
    )
