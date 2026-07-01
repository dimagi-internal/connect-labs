"""Push the Verified Monitoring render template to the live workflow.

The render source of truth is the repo file
``connect_labs/workflow/templates/verified_monitoring_render.js``. This
helper ships it to the live workflow (def 3699 on synthetic opp 10008) via the
``connect_labs`` MCP — the no-deploy render loop. It fetches the current
``render_code_version`` with ``workflow_get`` and pushes with
``workflow_update_render_code`` using that as ``expected_version`` (optimistic
concurrency). Reuses the same minimal MCP client as ``regenerate.py``.

Usage::

    export LABS_MCP_TOKEN=...     # or read from ~/.claude/mcp.json
    python scripts/walkthroughs/verified-monitoring/push_render.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

# Reuse the MCP client + token resolution from regenerate.py (same dir).
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from regenerate import MCP_URL, _call, _token  # noqa: E402

REPO_ROOT = HERE.parents[2]
RENDER_FILE = REPO_ROOT / "connect_labs/workflow/templates/verified_monitoring_render.js"
OPP, WF = 10008, 3699


def _session(c: httpx.Client, headers: dict) -> dict:
    r = c.post(
        MCP_URL,
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "push-render", "version": "1"},
            },
        },
    )
    sid = r.headers.get("mcp-session-id") or r.headers.get("Mcp-Session-Id")
    h = dict(headers)
    if sid:
        h["Mcp-Session-Id"] = sid
    c.post(MCP_URL, headers=h, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    return h


def main() -> int:
    code = RENDER_FILE.read_text()
    marker = next((ln for ln in code.splitlines() if "VERIFIED_MONITORING_RENDER_V" in ln), "")
    print(f"pushing {RENDER_FILE.name} ({len(code)} chars) · {marker.strip()}")

    token = _token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    with httpx.Client(timeout=180) as c:
        h = _session(c, headers)

        got, err = _call(c, h, "workflow_get", {"workflow_id": WF, "opportunity_id": OPP})
        if err:
            print("workflow_get ERROR:", json.dumps(got, default=str)[:500])
            return 1
        ver = None
        if isinstance(got, dict):
            ver = got.get("render_code_version") or got.get("version")
        print(f"current render_code_version={ver}")

        res, err = _call(
            c,
            h,
            "workflow_update_render_code",
            {
                "workflow_id": WF,
                "opportunity_id": OPP,
                "component_code": code,
                "expected_version": ver,
            },
        )
        if err:
            print("workflow_update_render_code ERROR:", json.dumps(res, default=str)[:500])
            return 1
        newver = res.get("render_code_version") if isinstance(res, dict) else res
        print(f"pushed OK · new render_code_version={newver}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
