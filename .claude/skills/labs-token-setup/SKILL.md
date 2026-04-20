---
name: labs-token-setup
description: Use when the user wants to set up or refresh their labs MCP Personal Access Token (PAT). Triggers on "set up labs mcp", "I need a labs token", "configure claude for labs", "my labs token expired", "connect me to labs". Opens the labs UI to approve a new token, receives it via a localhost callback, and writes it to ~/.claude/mcp.json.
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
   - Open `<labs_base_url>/mcp/admin/create-token/?callback=...&state=...` in the user's default browser.
   - Wait up to 120 seconds for the callback.
   - On receipt, validate state nonce, merge the token into `~/.claude/mcp.json` under the `connect_labs` server key, and print the resulting config.

4. When the script finishes successfully, tell the user to **fully restart Claude Code** (not just reload a tab — the MCP config is read on process start). The session they're in will NOT see the new server until restart.

5. On failure modes:
   - **Timeout:** user didn't complete the browser flow. Rerun the script.
   - **HTTP 400 from labs:** the script is misconfigured or the labs URL is wrong. Check the output.
   - **HTTP 302 → /accounts/login/:** user wasn't logged into labs. Have them log in, then rerun.
   - **Existing `connect_labs` entry in mcp.json:** the script overwrites it. Confirm with the user if they have a valid existing token they want to keep.

## Anti-patterns

- Do NOT try to create tokens via `python manage.py mcp_create_token` unless the user explicitly asks — that requires shell access to the labs host. The browser flow works for any environment the user can reach with a browser.
- Do NOT hand-edit `~/.claude/mcp.json` — let the script do it. It handles JSON merge and keeps other server configs untouched.
- Do NOT show the raw token to the user unless they ask — it's secret. The script prints the JSON config with the token embedded, which is unavoidable, but keep it ephemeral.
