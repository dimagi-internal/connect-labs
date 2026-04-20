#!/usr/bin/env python3
"""Helper for the labs-token-setup skill.

Starts a localhost listener on a random port, opens the labs MCP token
creation page in the user's browser with callback + state params, waits for
the redirect, and writes the resulting token to ~/.claude/mcp.json.

Usage:
    python setup_labs_token.py <labs_base_url>

Example:
    python setup_labs_token.py https://labs.connect.dimagi.com
"""
from __future__ import annotations

import json
import secrets
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

TIMEOUT_SECONDS = 120
MCP_CONFIG_PATH = Path.home() / ".claude" / "mcp.json"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Result:
    token: str | None = None
    name: str | None = None
    error: str | None = None


class _CallbackHandler(BaseHTTPRequestHandler):
    result: _Result
    expected_state: str

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/cb":
            self._respond(404, "Not found")
            return

        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}

        if params.get("state") != self.expected_state:
            self.result.error = "state mismatch — possible CSRF, ignoring"
            self._respond(400, "State mismatch")
            return

        token = params.get("token")
        name = params.get("name")
        if not token:
            self.result.error = "no token in callback"
            self._respond(400, "No token in callback")
            return

        self.result.token = token
        self.result.name = name or "claude-code"
        self._respond(
            200,
            "<html><body style='font-family: sans-serif; text-align: center; padding: 4rem;'>"
            "<h1>Token received</h1>"
            "<p>You can close this tab and return to your terminal.</p>"
            "</body></html>",
        )

    def _respond(self, status: int, body: str):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format, *args):  # noqa: A002, ARG002 — silence stdlib server logging
        return


def _start_listener(port: int, result: _Result, state: str) -> HTTPServer:
    handler = type(
        "BoundHandler",
        (_CallbackHandler,),
        {"result": result, "expected_state": state},
    )
    server = HTTPServer(("127.0.0.1", port), handler)
    server.timeout = 1  # poll interval for handle_request
    return server


def _wait_for_callback(server: HTTPServer, result: _Result) -> None:
    """Block until a callback arrives or the timeout elapses."""
    import time

    start = time.monotonic()
    while result.token is None and result.error is None:
        if time.monotonic() - start > TIMEOUT_SECONDS:
            result.error = f"timed out after {TIMEOUT_SECONDS}s waiting for callback"
            return
        server.handle_request()


def _update_mcp_config(raw_token: str, labs_base_url: str) -> None:
    MCP_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    if MCP_CONFIG_PATH.exists():
        try:
            config = json.loads(MCP_CONFIG_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raise SystemExit("~/.claude/mcp.json exists but is not valid JSON. " "Fix or remove it before retrying.")
    else:
        config = {}

    servers = config.setdefault("mcpServers", {})
    servers["connect_labs"] = {
        "type": "http",
        "url": labs_base_url.rstrip("/") + "/mcp/",
        "headers": {"Authorization": f"Bearer {raw_token}"},
    }

    MCP_CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def main(labs_base_url: str) -> int:
    if not labs_base_url.startswith(("http://", "https://")):
        print(
            f"error: labs base URL must start with http:// or https://, got {labs_base_url!r}",
            file=sys.stderr,
        )
        return 2

    port = _find_free_port()
    state = secrets.token_urlsafe(24)
    result = _Result()

    server = _start_listener(port, result, state)
    t = threading.Thread(target=_wait_for_callback, args=(server, result), daemon=True)
    t.start()

    from urllib.parse import urlencode

    consent_url = (
        labs_base_url.rstrip("/")
        + "/mcp/admin/create-token/?"
        + urlencode(
            {
                "callback": f"http://127.0.0.1:{port}/cb",
                "state": state,
            }
        )
    )

    print(f"Opening browser to {labs_base_url}/mcp/admin/create-token/ ...")
    print(f"Listening on http://127.0.0.1:{port} for callback (timeout: {TIMEOUT_SECONDS}s)")
    webbrowser.open(consent_url)

    t.join()

    if result.error:
        print(f"error: {result.error}", file=sys.stderr)
        return 1

    assert result.token  # if we got here, success
    _update_mcp_config(result.token, labs_base_url)
    print()
    print(f"Token '{result.name}' created and written to {MCP_CONFIG_PATH}")
    print("Restart Claude Code for the new server to become active.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: setup_labs_token.py <labs_base_url>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
