"""Minimal shared MCP-over-HTTP client for walkthrough seeders.

Walkthrough setup scripts (the canopy ``setup:`` block runs them per render) seed
and reset labs-only synthetic opportunities (opp id >= 10_000). Those records live
in the labs prod DB behind the local-records backend; the ``connect_labs`` MCP
reaches them in-app. As of PR #678 the microplans + solicitation MCP tools route
labs-only opps to the local backend and grant access to opted-in callers, so
seeders talk to the MCP directly over HTTP instead of shelling out to
``aws ecs run-task`` (no AWS session required).

Usage::

    from scripts.walkthroughs._mcp_client import token, session, call
    import httpx

    with httpx.Client(timeout=600) as c:
        h = session(c, token())
        result, is_error = call(c, h, "microplans_list_plans", {"program_id": 10008})
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

MCP_URL = os.environ.get("LABS_MCP_URL", "https://labs.connect.dimagi.com/mcp/")


def token() -> str:
    """Resolve the labs MCP bearer token from env or the Claude MCP config."""
    tok = os.environ.get("LABS_MCP_TOKEN")
    if tok:
        return tok
    for cfg in (Path.home() / ".claude.json", Path.home() / ".claude" / "mcp.json"):
        if not cfg.exists():
            continue
        data = json.loads(cfg.read_text())
        servers = data.get("mcpServers", data.get("servers", {}))
        for name, spec in servers.items():
            if "connect_labs" in name or "labs" in name:
                hdrs = spec.get("headers", {}) or {}
                auth = hdrs.get("Authorization", hdrs.get("authorization", ""))
                if auth.startswith("Bearer "):
                    return auth[len("Bearer ") :]
    sys.exit("No MCP token: set LABS_MCP_TOKEN or configure connect_labs in ~/.claude.json")


def _parse(r: httpx.Response) -> dict:
    for ln in r.text.splitlines():
        if ln.startswith("data:"):
            try:
                return json.loads(ln[5:].strip())
            except Exception:
                pass
    try:
        return r.json()
    except Exception:
        return {"_raw": r.text[:400]}


def session(c: httpx.Client, bearer: str) -> dict:
    """Open an MCP session; return the headers (with Mcp-Session-Id) for ``call``."""
    headers = {
        "Authorization": f"Bearer {bearer}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
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
                "clientInfo": {"name": "walkthrough-seeder", "version": "1"},
            },
        },
    )
    sid = r.headers.get("mcp-session-id") or r.headers.get("Mcp-Session-Id")
    h = dict(headers)
    if sid:
        h["Mcp-Session-Id"] = sid
    c.post(MCP_URL, headers=h, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    return h


def call(c: httpx.Client, h: dict, name: str, args: dict):
    """Call an MCP tool. Returns ``(result, is_error)``."""
    r = c.post(
        MCP_URL,
        headers=h,
        json={"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": name, "arguments": args}},
    )
    res = _parse(r).get("result", {})
    sc = res.get("structuredContent")
    if sc is not None:
        return sc, res.get("isError")
    cont = res.get("content")
    if isinstance(cont, list) and cont and "text" in cont[0]:
        try:
            return json.loads(cont[0]["text"]), res.get("isError")
        except Exception:
            return cont[0]["text"], res.get("isError")
    return res, res.get("isError")
