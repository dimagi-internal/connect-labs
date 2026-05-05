"""Shared helpers for rendering MCP client config snippets.

Single source of truth for the ~/.claude/mcp.json shape that gets shown to
users after token creation — used by both the management command and the
self-service token UI.
"""
from __future__ import annotations

DEFAULT_SERVER_URL = "https://labs.connect.dimagi.com/mcp/"


def build_mcp_json_snippet(raw_token: str, server_url: str = DEFAULT_SERVER_URL) -> str:
    return (
        "{\n"
        '  "mcpServers": {\n'
        '    "connect_labs": {\n'
        '      "type": "http",\n'
        f'      "url": "{server_url}",\n'
        '      "headers": {\n'
        f'        "Authorization": "Bearer {raw_token}"\n'
        "      }\n"
        "    }\n"
        "  }\n"
        "}\n"
    )
