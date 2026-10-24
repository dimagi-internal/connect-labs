"""A minimal client for labs' MCP server, as ace (its Personal Access Token).

Streamable HTTP, stateless JSON-RPC: every call is one POST whose answer arrives as
a single SSE ``message`` event. The harness uses it for ground truth (``data_query``)
and to find the synthetic opportunities and workflows a scenario points at, so the
numbers a check compares against come from the same engine the chat uses, as the
same person.
"""

from __future__ import annotations

import itertools
import json
import os
from pathlib import Path

import httpx

DEFAULT_URL = "https://labs.connect.dimagi.com/mcp/"
#: Where ace keeps its PAT (written by the ace plugin's /ace:setup and labs-token-mint).
ENV_FILES = (Path.home() / ".ace" / ".env", Path.home() / ".claude" / "plugins" / "data" / "ace-ace" / ".env")


class MCPError(RuntimeError):
    pass


def ace_token() -> str:
    """ace's labs PAT: ``LABS_MCP_TOKEN`` from the environment, else from ace's .env."""
    if os.environ.get("LABS_MCP_TOKEN"):
        return os.environ["LABS_MCP_TOKEN"]
    for path in ENV_FILES:
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            if line.startswith("LABS_MCP_TOKEN="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value
    raise MCPError(
        "No labs PAT for ace: set LABS_MCP_TOKEN or run the ace plugin's /ace:labs-token-mint "
        "(it writes LABS_MCP_TOKEN into ~/.ace/.env)."
    )


class LabsMCP:
    def __init__(self, token: str | None = None, url: str = DEFAULT_URL, timeout: float = 180.0):
        self.url = url
        self._ids = itertools.count(1)
        self._http = httpx.Client(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {token or ace_token()}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )

    def _rpc(self, method: str, params: dict) -> dict:
        body = {"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params}
        resp = self._http.post(self.url, content=json.dumps(body))
        if resp.status_code != 200:
            raise MCPError(f"{method}: HTTP {resp.status_code}: {resp.text[:300]}")
        payload = None
        if resp.headers.get("content-type", "").startswith("text/event-stream"):
            for line in resp.text.splitlines():
                if line.startswith("data:"):
                    payload = json.loads(line[5:].strip())
        else:
            payload = resp.json()
        if payload is None:
            raise MCPError(f"{method}: empty response")
        if "error" in payload:
            raise MCPError(f"{method}: {payload['error']}")
        return payload["result"]

    def call(self, name: str, arguments: dict | None = None) -> dict | list:
        """Call a tool and return its decoded JSON result (raises on a tool error)."""
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        structured = result.get("structuredContent")
        texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        if result.get("isError"):
            raise MCPError(f"{name}: {' '.join(texts)[:600]}")
        if structured is not None:
            return structured.get("result", structured) if set(structured) == {"result"} else structured
        return _decode(texts[0] if texts else "null")

    def query(self, sql: str, opportunity_ids: list[int], max_rows: int = 1000) -> dict:
        """``data_query`` as ace, every row inline (ace is not canopy, so nothing is kept)."""
        return self.call("data_query", {"sql": sql, "opportunity_ids": opportunity_ids, "max_rows": max_rows})


def _decode(text: str):
    """Tool text is JSON, sometimes JSON-in-a-content-list (labs wraps some results twice)."""
    value = json.loads(text)
    if isinstance(value, list) and value and isinstance(value[0], dict) and value[0].get("type") == "text":
        return _decode(value[0]["text"])
    return value
