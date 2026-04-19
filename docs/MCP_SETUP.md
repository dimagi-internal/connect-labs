# Labs MCP Setup

This guide gets Claude Code connected to the labs MCP server so you can iterate
on workflows and pipelines without copy-pasting through the web UI.

## Prerequisites

- An account on labs (`labs.connect.dimagi.com`).
- Claude Code installed (CLI, desktop, or IDE extension). Web (claude.ai/code)
  also works.
- Shell access to the labs host, *or* ask an admin to create a token for you.

## 1. Create a Personal Access Token

An admin (or you, if you have shell access) runs:

    python manage.py mcp_create_token --user <your-username> --name <label>

The command prints a `Token: <raw>` line **once**. Copy it immediately — it
cannot be retrieved later. If you lose it, create a new one.

## 2. Add the server to `.claude/mcp.json`

The `mcp_create_token` command prints a ready-to-paste snippet. Drop it into
your `~/.claude/mcp.json` (or your project's `.claude/mcp.json`):

    {
      "mcpServers": {
        "connect_labs": {
          "type": "http",
          "url": "https://labs.connect.dimagi.com/mcp/",
          "headers": {
            "Authorization": "Bearer <your-raw-token>"
          }
        }
      }
    }

If you already have other MCP servers configured, merge under the same
`mcpServers` key.

## 3. Restart Claude Code

Restart the CLI, desktop app, IDE, or reload the web tab. Check that Claude
can list the labs tools:

> "List available labs MCP tools"

In Plan 1 the catalog is empty — you'll see no tools yet. Plan 2 (workflow +
pipeline tools) is what actually enables iteration. This step just verifies
connectivity and auth.

## Troubleshooting

**401 Unauthorized** — Token missing, typoed, expired, or revoked. Create
a new one.

**Cannot connect** — Confirm the URL. Some corporate networks block labs;
try from a non-corp network to isolate.

**Unexpected tool failures in Plan 2+** — Check `https://labs.connect.dimagi.com/admin/mcp/mcpauditlog/`
(if you have admin access). Every tool call is logged with the error code.

## Token hygiene

- Treat your PAT like a password. Store it in a password manager or your
  OS keychain. Do not commit to git.
- Default lifetime is 90 days. Pass `--ttl-days 0` at creation for no expiry,
  but rotate periodically.
- Admins can revoke any token in the Django admin at
  `/admin/mcp/mcpaccesstoken/`.

## Which MCP surface?

| Surface | Supported? | Notes |
|---|---|---|
| Claude Code CLI | Yes | stdio + remote both work |
| Claude Code desktop (Mac/Windows) | Yes | same `.claude/mcp.json` |
| IDE extensions (VS Code, JetBrains) | Yes | same config |
| Claude web (claude.ai/code) | Yes | this is the reason we went remote |
