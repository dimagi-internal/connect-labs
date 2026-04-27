#!/usr/bin/env python3
"""Helper for the labs-token-setup skill.

Starts a localhost listener on a random port, opens the labs MCP token
creation page in the user's browser with callback + state params, waits for
the redirect, and registers the resulting token with Claude Code by shelling
out to `claude mcp add --scope user`.

Why shell out instead of editing JSON directly? Claude Code stores user-scope
MCP servers in ~/.claude.json (a large file with lots of unrelated state), not
~/.claude/mcp.json. Using the CLI keeps us forward-compatible with any future
config layout changes.

Usage:
    python setup_labs_token.py <labs_base_url>

Example:
    python setup_labs_token.py https://labs.connect.dimagi.com
"""
from __future__ import annotations

import secrets
import shutil
import socket
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

TIMEOUT_SECONDS = 120
SERVER_NAME = "connect_labs"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _is_wsl() -> bool:
    try:
        with open("/proc/version", encoding="utf-8") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


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


def _register_mcp_server(raw_token: str, labs_base_url: str) -> None:
    """Register the connect_labs server with Claude Code via `claude mcp add`.

    Removes any existing user-scope entry first so we always overwrite cleanly —
    the CLI errors out on duplicate names otherwise.
    """
    claude_bin = shutil.which("claude")
    if not claude_bin:
        raise SystemExit(
            "error: `claude` CLI not found on PATH. This script registers the MCP\n"
            "server via `claude mcp add`. Install Claude Code or add it to PATH,\n"
            "then rerun."
        )

    mcp_url = labs_base_url.rstrip("/") + "/mcp/"
    auth_header = f"Authorization: Bearer {raw_token}"

    # Remove any existing user-scope entry so `claude mcp add` doesn't fail on
    # duplicate name. Ignore errors — it's fine if nothing was there.
    subprocess.run(
        [claude_bin, "mcp", "remove", "--scope", "user", SERVER_NAME],
        capture_output=True,
        text=True,
        check=False,
    )

    proc = subprocess.run(
        [
            claude_bin,
            "mcp",
            "add",
            "--transport",
            "http",
            "--scope",
            "user",
            SERVER_NAME,
            mcp_url,
            "--header",
            auth_header,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(
            "error: `claude mcp add` failed.\n" f"stdout: {proc.stdout.strip()}\n" f"stderr: {proc.stderr.strip()}"
        )


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

    print()
    print("Open this URL in your browser to approve the token:")
    print()
    print(f"    {consent_url}")
    print()
    if _is_wsl():
        print("Detected WSL — copy the URL above into your Windows browser.")
        print("(Auto-open from WSL usually can't reach a Windows browser, so this")
        print(" script won't try. The localhost callback will still work via WSL2's")
        print(" automatic localhost forwarding.)")
    else:
        webbrowser.open(consent_url)
        print("(Also attempting to open it in your default browser.)")
    print()
    print(f"Listening on http://127.0.0.1:{port} for callback (timeout: {TIMEOUT_SECONDS}s) ...")

    t.join()

    if result.error:
        print(f"error: {result.error}", file=sys.stderr)
        return 1

    assert result.token  # if we got here, success
    _register_mcp_server(result.token, labs_base_url)
    print()
    print(f"Token '{result.name}' created and registered as MCP server '{SERVER_NAME}' (user scope).")
    print("Restart Claude Code for the new server to become active.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: setup_labs_token.py <labs_base_url>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
