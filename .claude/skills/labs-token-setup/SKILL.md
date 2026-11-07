---
name: labs-token-setup
description: Use when the user wants to set up or refresh their labs MCP Personal Access Token (PAT), or asks an agent to get short-lived labs access for one job (from their own computer, or from a cloud runner or remote box). Triggers on "set up labs mcp", "I need a labs token", "configure claude for labs", "my labs token expired", "connect me to labs", "request a short-lived token", "get a temporary token on my behalf". Opens the labs UI to approve a new token, receives it via a localhost callback, and registers a `connect_labs` MCP server in Claude Code via `claude mcp add --scope user`.
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

   python .claude/skills/labs-token-setup/setup_labs_token.py <labs_base_url> [--no-uservisit-data] [--temporary [--output PATH]]

   Pass `--no-uservisit-data` when the user wants a token that never sees user
   visit data: it reads definitions (workflows, pipelines, indicator registries,
   app structure, solicitations, the org directory, targeting), and can edit
   workflow and indicator definitions and trigger saved-run generation, but
   never reads visit rows or visit-derived values (except on generated synthetic
   opportunities). It reaches exactly what the `/mcp/no_user_visit/` address
   does. The consent page names the access level. The tool list is
   `connect_labs/mcp/token_scopes.py`.

   The script will:

   - Pick a random unused localhost port + a state nonce.
   - Start a tiny HTTP listener on 127.0.0.1:PORT for the callback.
   - Print the full `<labs_base_url>/mcp/admin/create-token/?callback=...&state=...` URL to the terminal so the user can copy-paste it into any browser (essential for WSL, SSH, and headless sessions).
   - On non-WSL systems, also attempt `webbrowser.open()` as a convenience. On WSL the auto-open is skipped with a note, since it usually can't reach the Windows browser — the user copies the printed URL instead. The localhost callback still works via WSL2's automatic localhost forwarding.
   - Wait up to 300 seconds for the callback. A **full-access** token sends the user to canopy on the way, to approve it with their passkey; a no-user-visit one does not.
   - On receipt, validate the state nonce, then shell out to `claude mcp add --transport http --scope user connect_labs <labs_base_url>/mcp/ --header "Authorization: Bearer <token>"`. Any existing `connect_labs` user-scope entry is removed first so the add cleanly overwrites.

   > **Why not edit `~/.claude/mcp.json` directly?** Claude Code stores user-scope MCP servers in `~/.claude.json` (a large file with many unrelated keys), not in a separate `mcp.json`. Using the `claude mcp add` CLI is the supported interface and is forward-compatible with future config layout changes.

4. When the script finishes successfully, tell the user to **fully restart Claude Code** (not just reload a tab — the MCP config is read on process start). The session they're in will NOT see the new server until restart. They can verify with `claude mcp list` — `connect_labs` should appear with a ✓ Connected status.

5. On failure modes:
   - **Timeout:** user didn't complete the browser flow. Rerun the script.
   - **HTTP 400 from labs:** the script is misconfigured or the labs URL is wrong. Check the output.
   - **HTTP 302 → /accounts/login/:** user wasn't logged into labs. Have them log in, then rerun.
   - **`claude` CLI not on PATH:** the script errors out with a clear message. The user needs Claude Code installed and on PATH.
   - **Existing `connect_labs` user-scope entry:** the script removes it first and then adds fresh, so any previous token is silently overwritten. If the user had a valid existing token they wanted to keep, confirm before rerunning.

## Temporary access for one job

When the person asks you to get short-lived access for something your own labs tools cannot reach (they run as another identity, and it 404s), use `--temporary`, and add `--no-uservisit-data` if the job does not need visit data:

    python .claude/skills/labs-token-setup/setup_labs_token.py https://labs.connect.dimagi.com --temporary --output <scratchpad>/labs-token

- The token lasts one hour and cannot renew. The approval page says so.
- It does NOT touch Claude Code's config: the person's `connect_labs` server keeps its token, and this session's MCP tools do not change. Call the server yourself with `Authorization: Bearer $(cat <file>)`.
- A full-access token still needs the person's passkey. Tell them what you are about to read and why before you start.
- Keep the token out of your output, and delete the file when the job is done.

### An agent that is NOT on the person's computer (a cloud runner, a remote box)

`--temporary` returns the token to `127.0.0.1`, so it only works when the person's browser and
the agent share a machine. From anywhere else use `request_agent_token.py`: the agent asks with
its own labs token, the person approves on a labs page with their passkey (through canopy, like
any full-access token), and the token comes back sealed to a key only the agent holds. With `--prove-runner` the helper also
signs the request with the runner's device key, so the person's page names the runner (opt-in).

    python request_agent_token.py ask https://labs.connect.dimagi.com --reason "read how the CHC reports compute coverage"

Add `--level full` only if the job needs visit data; the default is no-user-visit. Either level
needs the passkey. It prints `{ref, approve_url}`. **Show the person the link as a message before
you wait**, saying what you will read, then:

    python request_agent_token.py collect <ref> --output <scratchpad>/labs-token --wait 240

Exit 4 means still waiting (run `collect` again if they say they are on it), 3 means declined or
expired. The token is the PERSON's, lasts one hour and shows on their MCP access page. The agent's
own labs token (`LABS_MCP_TOKEN` or `--token-file`) signs both calls; the script needs the
`cryptography` package. Same rules as above: keep the token out of your output, delete the file
when the job is done, and say what you read with it.

## Anti-patterns

- Do NOT write your own OAuth script against `/o/register/` and `/o/authorize/` to get access. That sign-in is for MCP apps a person adds themselves: it lasts until disconnected and leaves a registered app behind. An app's name also limits nothing, so a name like "read one saved run" misleads the person approving it.

- Do NOT try to create tokens via `python manage.py mcp_create_token` unless the user explicitly asks — that requires shell access to the labs host. The browser flow works for any environment the user can reach with a browser.
- Do NOT hand-edit `~/.claude.json` or `~/.claude/mcp.json` — let the script call `claude mcp add`. Hand-editing `~/.claude.json` risks clobbering unrelated user state; writing to `~/.claude/mcp.json` doesn't register the server at all (Claude Code doesn't read that path).
- Do NOT show the raw token to the user unless they ask — it's secret. The script registers it silently and does not print it.
