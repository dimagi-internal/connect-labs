---
name: labs-token-setup
description: Use when the user wants to set up or refresh their labs MCP Personal Access Token (PAT). Triggers on "set up labs mcp", "I need a labs token", "configure claude for labs", "my labs token expired", "connect me to labs". Opens the labs UI to approve a new token, receives it via a localhost callback, and registers a `connect_labs` MCP server in Claude Code via `claude mcp add --scope user`.
---

# Labs MCP Token Setup

Seamless flow for creating an MCP Personal Access Token and wiring it into Claude Code's config.

## Procedure

1. Ask the user which labs environment (unless it's obvious from context):

   - Production: `https://labs.connect.dimagi.com`
   - Staging: whatever staging URL they give you
   - Local dev: `http://127.0.0.1:8000` (only if they're running labs locally)

2. Before running the setup script, verify the user has already logged into labs in a browser for that environment. If unclear, tell them to open the URL first (without the `/mcp/admin/create-token/` path) to establish a session, then come back.

3. Run the bundled helper script, passing the labs base URL:

   python .claude/skills/labs-token-setup/setup_labs_token.py <labs_base_url>

   The script will:

   - Pick a random unused localhost port + a state nonce.
   - Start a tiny HTTP listener on 127.0.0.1:PORT for the callback.
   - Print the full `<labs_base_url>/mcp/admin/create-token/?callback=...&state=...` URL to the terminal so the user can copy-paste it into any browser (essential for WSL, SSH, and headless sessions).
   - On non-WSL systems, also attempt `webbrowser.open()` as a convenience. On WSL the auto-open is skipped with a note, since it usually can't reach the Windows browser — the user copies the printed URL instead. The localhost callback still works via WSL2's automatic localhost forwarding.
   - Wait up to 120 seconds for the callback.
   - On receipt, validate the state nonce, then shell out to `claude mcp add --transport http --scope user connect_labs <labs_base_url>/mcp/ --header "Authorization: Bearer <token>"`. Any existing `connect_labs` user-scope entry is removed first so the add cleanly overwrites.

   > **Why not edit `~/.claude/mcp.json` directly?** Claude Code stores user-scope MCP servers in `~/.claude.json` (a large file with many unrelated keys), not in a separate `mcp.json`. Using the `claude mcp add` CLI is the supported interface and is forward-compatible with future config layout changes.

4. When the script finishes successfully, tell the user to **fully restart Claude Code** (not just reload a tab — the MCP config is read on process start). The session they're in will NOT see the new server until restart. They can verify with `claude mcp list` — `connect_labs` should appear with a ✓ Connected status.

5. On failure modes:
   - **Timeout:** user didn't complete the browser flow. Rerun the script.
   - **HTTP 400 from labs:** the script is misconfigured or the labs URL is wrong. Check the output.
   - **HTTP 302 → /accounts/login/:** user wasn't logged into labs. Have them log in, then rerun.
   - **`claude` CLI not on PATH:** the script errors out with a clear message. The user needs Claude Code installed and on PATH.
   - **Existing `connect_labs` user-scope entry:** the script removes it first and then adds fresh, so any previous token is silently overwritten. If the user had a valid existing token they wanted to keep, confirm before rerunning.

## Anti-patterns

- Do NOT try to create tokens via `python manage.py mcp_create_token` unless the user explicitly asks — that requires shell access to the labs host. The browser flow works for any environment the user can reach with a browser.
- Do NOT hand-edit `~/.claude.json` or `~/.claude/mcp.json` — let the script call `claude mcp add`. Hand-editing `~/.claude.json` risks clobbering unrelated user state; writing to `~/.claude/mcp.json` doesn't register the server at all (Claude Code doesn't read that path).
- Do NOT show the raw token to the user unless they ask — it's secret. The script registers it silently and does not print it.
