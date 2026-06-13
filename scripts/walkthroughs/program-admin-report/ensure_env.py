"""Setup entrypoint for the Program Admin Report walkthrough.

The single ``setup:`` command the canopy walkthrough invokes before
rendering. Modeled on the older ``regenerate.py``, but driven by the
declarative composite ENV manifest
(``commcare_connect/labs/synthetic/envs/program-admin-report.yaml``)
instead of ``demo_config.json``. One run does all of:

1. **Ensure** — call the ``synthetic_env_ensure`` MCP tool on labs with
   ``env="program-admin-report"``. The tool executes server-side, inside
   the labs app, so the labs-only synthetic opps (10000/10001) are written
   through the local-records backend on the labs DB — the only transport
   that actually reaches labs prod for synthetic opportunities. (A local
   ``python -m ...ensure`` would seed YOUR local dev DB, not labs prod —
   the bug this script fixes.) The tool returns the realized id map: the
   flat ``${...}`` vars the walkthrough spec interpolates.

2. **Freshness preflight** — fetch the freshly-realized run pages over HTTP
   (labs session cookies) and compare the served ``render_code`` against the
   local checkout's templates (AST-extracted). Aborts loudly when labs is
   serving stale template code — the 2-4 min ECS worker-cutover lag after a
   deploy "succeeds". Wait and re-run. ``SKIP_FRESHNESS=1`` bypasses the
   check (DANGEROUS — you'll record or grade a UI that doesn't match the
   code you think is live).

3. **Emit** the realized map to ``realized.json`` (the file the walkthrough
   ``${...}`` substitution reads). The ensure engine produces this map
   directly — par ids/urls, the good/incomplete drill targets, the
   current-week ("wk4") run, archetype-derived FLW usernames — so unlike
   ``regenerate.py`` this script does no post-hoc discovery; it just persists
   what the engine realized.

Requirements (same as regenerate.py):

- ``LABS_MCP_TOKEN`` — a labs MCP PAT (mint at ``/labs/mcp/tokens/``; NOT
  the Connect OAuth token), or a configured ``connect_labs`` server in
  ``~/.claude.json``.
- A labs browser session file at ``~/.ace/labs-session.json`` (run
  ``/ace:labs-login``; override via ``LABS_SESSION_FILE``) — the run pages
  are session-auth'd, so the freshness fetch needs the cookie session.

Usage::

    python scripts/walkthroughs/program-admin-report/ensure_env.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from walkthroughs._lib import config as wcfg  # noqa: E402
from walkthroughs._lib.freshness import (  # noqa: E402
    assert_served_current,
    served_render_code_from_html,
    skip_requested,
)
from walkthroughs._lib.labs_mcp import LabsMCPSession  # noqa: E402

ENV_NAME = "program-admin-report"
REALIZED_PATH = HERE / "realized.json"


def _labs_http_client() -> httpx.Client:
    """Session-cookie HTTP client for the labs run pages.

    Reuses the recorders' Playwright storage state (``/ace:labs-login``) —
    the run pages are session-auth'd, so the MCP PAT won't do.
    """
    cookies = wcfg.session_cookies()
    if "sessionid" not in cookies:
        raise SystemExit(
            f"ERROR: no labs sessionid cookie in {wcfg.session_path()}. "
            "Run /ace:labs-login to refresh the labs session, then re-run."
        )
    return httpx.Client(timeout=60, cookies=cookies, follow_redirects=True)


def _check_freshness(client: httpx.Client, path: str, template_type: str, *, label: str) -> None:
    """Fetch a run page and assert it serves the local checkout's render_code."""
    if skip_requested():
        print(f"  !! SKIP_FRESHNESS=1 — skipping {template_type} freshness fetch ({label}). DANGEROUS.")
        return
    url = f"{wcfg.LABS_BASE_URL}{path}"
    resp = client.get(url)
    served = served_render_code_from_html(resp.text)
    if served is None:
        hint = ""
        if "login" in str(resp.url).lower() or resp.status_code in (401, 403):
            hint = " The labs session looks expired — run /ace:labs-login and retry."
        raise SystemExit(
            f"ERROR: could not read the served render_code from {url} "
            f"(status {resp.status_code}).{hint} "
            "(SKIP_FRESHNESS=1 bypasses this preflight — dangerous.)"
        )
    try:
        assert_served_current(served, template_type, label=label)
    except RuntimeError as e:
        raise SystemExit(str(e))


def main() -> int:
    # ---------------- 1. Ensure (server-side, via the MCP tool) ---------- #
    print(f"Ensuring synthetic env {ENV_NAME!r} via synthetic_env_ensure on labs...")
    with LabsMCPSession() as mcp:
        result, is_error = mcp.tool("synthetic_env_ensure", {"env": ENV_NAME})
    if is_error or not isinstance(result, dict):
        print("synthetic_env_ensure ERROR:")
        print(json.dumps(result, indent=2, default=str)[:2000])
        return 1
    print(f"Realized {len(result)} variable(s).")

    # ---------------- 2. Freshness preflight ----------------------------- #
    # Abort loudly if labs is serving stale template code (the 2-4 min ECS
    # worker-cutover lag): the ensure run stamps each def's render_code from
    # the template the *running* worker has, so a stale worker writes stale
    # JSX. Wait for the cutover, then re-run this setup. The realized map
    # already carries the run-page paths the recorder will drive.
    par_url = result.get("par_url")
    wk4_url = result.get("wk4_url")
    if not par_url:
        print("ERROR: realized map has no par_url — cannot run the freshness preflight.")
        print(json.dumps(result, indent=2, default=str)[:2000])
        return 1

    print("\nFreshness preflight (served render_code vs local checkout)...")
    client = _labs_http_client()
    _check_freshness(client, par_url, "program_admin_report", label="PAR run page")
    if wk4_url:
        _check_freshness(client, wk4_url, "chc_nutrition_analysis", label="current-week in_progress run page")
    else:
        print("  ! no current-week (wk4_url) run in realized map — skipping chc_nutrition_analysis check")

    # ---------------- 3. Emit the realized vars JSON --------------------- #
    # The ensure engine's realized map IS the flat ${...} vars file the
    # walkthrough spec interpolates — write it verbatim (no generated_at
    # wrapper; the spec reads these keys directly).
    REALIZED_PATH.write_text(json.dumps(result, indent=2) + "\n")
    print(f"\nWrote {REALIZED_PATH}:")
    for k, v in result.items():
        print(f"  {k}={v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
