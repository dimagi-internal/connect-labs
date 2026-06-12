"""Minimal HTTP client for the ``connect_labs`` MCP server.

The walkthrough synthetic generators run server-side tools (e.g.
``program_admin_demo_seed``, ``workflow_create_run``) via the labs MCP
endpoint — that's the only correct transport for labs-only synthetic
opportunities: the tool executes inside the labs app, so
``LabsRecordAPIClient`` short-circuits to the local records backend on
the labs DB. A local in-process call would write to YOUR local dev DB
(or crash resolving the Connect token), never to labs prod.

Auth is a labs MCP Personal Access Token (PAT) — mint one at
``/labs/mcp/tokens/`` (or via the ``labs-token-setup`` skill). This is
NOT the Connect OAuth access token.

Token resolution order:

1. ``LABS_MCP_TOKEN`` env var.
2. The ``connect_labs`` server entry in ``~/.claude.json`` (or the older
   standalone ``~/.claude/mcp.json``) — the same token Claude Code uses.

Usage::

    from walkthroughs._lib.labs_mcp import LabsMCPSession

    with LabsMCPSession() as mcp:
        result, is_error = mcp.tool("program_admin_demo_seed", {...})
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

DEFAULT_MCP_URL = "https://labs.connect.dimagi.com/mcp/"


def mcp_url() -> str:
    return os.environ.get("LABS_MCP_URL", DEFAULT_MCP_URL)


def resolve_token() -> str:
    """Return a labs MCP PAT, or exit with instructions."""
    tok = os.environ.get("LABS_MCP_TOKEN")
    if tok:
        return tok
    for cfg in (Path.home() / ".claude.json", Path.home() / ".claude" / "mcp.json"):
        if not cfg.exists():
            continue
        try:
            data = json.loads(cfg.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        servers = data.get("mcpServers", data.get("servers", {}))
        for name, spec in servers.items():
            if "connect_labs" in name or "labs" in name:
                hdrs = spec.get("headers", {}) or {}
                auth = hdrs.get("Authorization", hdrs.get("authorization", ""))
                if auth.startswith("Bearer "):
                    return auth[len("Bearer ") :]
    sys.exit(
        "ERROR: no labs MCP token found. Set LABS_MCP_TOKEN (a labs MCP PAT "
        "from /labs/mcp/tokens/ — NOT the Connect OAuth token) or configure "
        "the connect_labs server in ~/.claude.json."
    )


def _parse_response(r: httpx.Response) -> dict:
    """Parse a JSON-RPC response that may arrive as SSE (``data:`` lines)."""
    for ln in r.text.splitlines():
        if ln.startswith("data:"):
            try:
                return json.loads(ln[5:].strip())
            except (json.JSONDecodeError, ValueError):
                pass
    try:
        return r.json()
    except (json.JSONDecodeError, ValueError):
        return {"_raw": r.text[:400]}


class LabsMCPSession:
    """An initialized MCP session: ``tool(name, args) -> (result, is_error)``."""

    def __init__(self, *, timeout: float = 900.0) -> None:
        self._client = httpx.Client(timeout=timeout)
        self._headers = {
            "Authorization": f"Bearer {resolve_token()}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        self._initialize()

    def _initialize(self) -> None:
        r = self._client.post(
            mcp_url(),
            headers=self._headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "walkthrough-synthetic-generator", "version": "1"},
                },
            },
        )
        sid = r.headers.get("mcp-session-id") or r.headers.get("Mcp-Session-Id")
        if sid:
            self._headers["Mcp-Session-Id"] = sid
        self._client.post(
            mcp_url(),
            headers=self._headers,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )

    def tool(self, name: str, args: dict) -> tuple[Any, bool]:
        """Call an MCP tool. Returns ``(result, is_error)``."""
        r = self._client.post(
            mcp_url(),
            headers=self._headers,
            json={
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {"name": name, "arguments": args},
            },
        )
        res = _parse_response(r).get("result", {})
        is_error = bool(res.get("isError"))
        sc = res.get("structuredContent")
        if sc is not None:
            return sc, is_error
        cont = res.get("content")
        if isinstance(cont, list) and cont and "text" in cont[0]:
            try:
                return json.loads(cont[0]["text"]), is_error
            except (json.JSONDecodeError, ValueError):
                return cont[0]["text"], is_error
        return res, is_error

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> LabsMCPSession:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
