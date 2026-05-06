# Safe-mode Claude Code (`inv safe-claude`)

> **Just want to edit a workflow?** See
> **[WORKFLOW_EDITOR_QUICKSTART.md](WORKFLOW_EDITOR_QUICKSTART.md)** — a
> step-by-step, non-technical walkthrough. This doc is the design / reference
> for people changing the safe-mode config itself.

A locked-down Claude Code session for working with PII through the labs MCP
servers. Policy-enforced at the Claude Code layer, not prompt-enforced —
nothing in the system prompt or user prompt can widen the tool surface.

Everything the session needs lives at the **project level** (checked into the
repo + `.env` rendered from 1Password), so any team member can clone and run
`inv safe-claude` without user-scope Claude Code configuration.

## What it is

`inv safe-claude` launches the Claude Code CLI with a rendered MCP config and
a pinned settings file that together:

- Route model calls through a **governed, ZDR-equivalent endpoint**. Auth
  is chosen by a **required** `--auth` flag each run (no default — the
  operator always picks explicitly):
  - `--auth=vertex`: Google Vertex AI, project `connect-labs`. The
    service-account JSON is fetched from 1Password (AI-Agents vault) into a
    0600 tempfile at launch and deleted on exit.
  - `--auth=api-key`: Anthropic ZDR API key, fetched fresh from 1Password
    on every launch. **1Password is the only source of truth** — the key
    is never read from `.env` or the parent shell.
  Either way, the task strips `ANTHROPIC_API_KEY` from the inherited child
  environment so Claude Code cannot fall back to a non-governed endpoint.
- Speak only to `connect_labs` and `commcare_hq_mcp` (no other MCP servers,
  no matter what's in your user-scope config).
- Operate in `dontAsk` permission mode: denied tools are silently blocked,
  unknown tools are silently blocked, no interactive "approve once?" dialog.
- Explicitly disable the two Claude Code escape hatches
  (`bypassPermissions` and `auto` modes).
- Ship with telemetry and non-essential model calls off.

## Prereqs

- Claude Code CLI installed and on `PATH` (`claude --version` works).
- 1Password access to the `AI-Agents` vault (for API key or Vertex credentials).
- A `connect_labs` PAT in `~/.claude.json`, registered via the `/labs-token-setup`
  skill. The PAT can also be passed as the `LABS_MCP_TOKEN` environment variable.

If you don't have a PAT yet, run `/labs-token-setup` in any Claude Code session.
Admins can also generate one on the labs host:

    python manage.py mcp_create_token --user <your-username> --name safe-claude

The `.env` file is **not required** for `inv safe-claude` — auth credentials come
from 1Password at launch. `.env` is only consulted for optional Vertex overrides
(cached service-account path, alternate project/region for testing).

## Run

    inv safe-claude

The task:

1. Fetches the auth secret from 1Password (API key or Vertex service-account JSON).
2. Reads `LABS_MCP_TOKEN` from the `LABS_MCP_TOKEN` env var or `~/.claude.json` (written by `/labs-token-setup`). The PAT is never read from `.env`.
3. Sets `LABS_MCP_TOKEN` as an environment variable — Claude Code expands the `${LABS_MCP_TOKEN}` placeholder in `safe-claude/mcp.json` at runtime, so the PAT is never written to disk.
4. Execs `claude --settings safe-claude/settings.json --mcp-config safe-claude/mcp.json --strict-mcp-config --permission-mode dontAsk`.
5. Deletes any ephemeral Vertex credentials tempfile when the session exits.

Verify the lockdown from inside the session:

> "What MCP servers and tools do you have available?"

You should see exactly `connect_labs` and `commcare_hq_mcp`, and tools
limited to `Read`, `Grep`, `Glob`, `TodoWrite`, `Skill`, `ToolSearch` plus
the `mcp__*` tools from the two servers.

## Per-setting review

Every key in `safe-claude/settings.json` and `safe-claude/mcp.json`, what
it does, and why it's set the way it is. If you're a reviewer, this is the
section to nitpick.

### `safe-claude/settings.json`

#### `permissions.defaultMode: "dontAsk"`

**What it does (from Claude Code docs):** allow-list tools run silently;
deny-list tools are silently blocked; tools matching no rule are **silently
blocked, no prompt**.

**Why we chose it:** the alternative `"default"` mode surfaces an interactive
"would you like to approve this once?" prompt for unclassified tools. In a
PII session, any such prompt is a potential operator foot-gun — a tired
reviewer clicking "allow" once can exfiltrate data that was meant to stay
put. `dontAsk` removes that escape.

**Could it be stricter?** `plan` mode is stricter still (every action
requires up-front plan approval). Rejected as too heavyweight for the
iterative workflow/pipeline use case.

#### `permissions.disableAutoMode: "disable"`

**What it does:** prevents Claude Code's `auto` permission mode from being
activated. `auto` uses an AI classifier to auto-approve "low-risk" actions
without prompting.

**Why we chose it:** the classifier's definition of low-risk is not our
definition of low-risk. Anything classified as low-risk that touches PII
should still be an explicit allow-list entry, not a classifier guess.

#### `permissions.disableBypassPermissionsMode: "disable"`

**What it does:** blocks entry into `bypassPermissions` mode, which would
skip all permission checks (reachable via `--dangerously-skip-permissions`).

**Why we chose it:** project-level settings override CLI flags for this key.
Without it, anyone could `claude --dangerously-skip-permissions --settings
safe-claude/settings.json ...` and defeat the whole lockdown.

#### `permissions.allow`

- **`mcp__connect_labs__*`** — labs CRUD and workflow/pipeline round-trip.
  The point of the session.
- **`mcp__commcare_hq_mcp__*`** — HQ app structure for pipeline schema
  authoring (`get_form_json_paths` etc.). Per CLAUDE.md, this is app
  definitions only — no form submissions, no case data, no PII.
- **`Read`, `Grep`, `Glob`** — read local repo files so the agent can
  reference existing templates, skills, and CLAUDE.md before drafting JSX
  or pipeline schemas. Read-only by design of each tool.
- **`TodoWrite`** — internal agent scratchpad. Stays inside the session.
- **`Skill`** — lets the agent invoke the `workflow-author`, `pipeline-author`
  skills we already rely on. Skills cannot widen the allow list; their own
  tool calls still route through this same permission check.
- **`ToolSearch`** — Claude Code's deferred-tool loader. Required for
  `Skill` to work. Loading a tool's schema is not the same as calling it —
  calls still hit allow/deny.

#### `permissions.deny`

- **`Write`, `Edit`, `NotebookEdit`** — the agent doesn't need to write
  local files. Workflow JSX and pipeline schemas round-trip through MCP
  (`workflow_update_render_code`, `pipeline_update_schema`). Denying local
  writes removes the possibility of dumping PII to disk.
- **`Bash`** — no shell, period. See "Sandbox" section below for why we
  chose full denial over a sandboxed shell.
- **`WebFetch`, `WebSearch`** — no outbound network path. Anthropic's ZDR
  workspace gets our prompts; nothing else should. Denies the two obvious
  exfiltration routes.
- **`Agent`** — blocks subagent spawning. Subagents inherit the same
  permissions, so this isn't a privilege escalation, but it prevents
  parallel contexts accumulating extra tool credits we haven't reviewed
  and keeps the audit trail linear.
- **`CronCreate`, `CronDelete`, `CronList`, `ScheduleWakeup`, `RemoteTrigger`** —
  scheduling tools. Denied because a scheduled or remotely-triggered future
  session would run with the user's default Claude Code config, not this
  safe-mode config. That's a persistence/escape route: a compromised prompt
  inside safe mode could schedule a later run that operates outside the
  lockdown.
- **`Read(./.env)`, `Read(./.env.*)`, `Read(./.gcp/**)`, `Read(~/.claude.json)`,
  `Read(~/.claude/**)`, `Grep(./.env)`, `Grep(./.env.*)`** — path-scoped
  denies that block reading project credential files while leaving `Read`/`Grep`
  open for repo source files. This closes a prompt-injection exfiltration
  path: without these, a malicious instruction embedded in a workflow
  definition retrieved via MCP could direct the model to read `.env` (which
  contains `COMMCARE_API_KEY`) and relay its contents to labs via an allowed
  MCP write tool. `Grep` is included alongside `Read` because it also returns
  file contents and is subject to the same vector.
- **`Read(~/.commcare-connect/**)`, `Glob(~/.commcare-connect/**)`,
  `Grep(~/.commcare-connect/**)`** — the Connect CLI stores its OAuth token
  here. An injection could read the file and forward the token via MCP write.
  All three access tools are denied to close the read → forward chain.
- **`Read(~/.aws/**)`, `Read(~/.ssh/**)`, `Read(~/.config/**)`** and their
  `Glob` equivalents — cloud and SSH credentials reachable via a Glob+Read
  chain even if direct Read is denied. Denied independently from the project
  `.env` rules because they live in the user home directory, not the project.
- **`mcp__Claude_in_Chrome__*`, `mcp__Claude_Preview__*`** — browser
  automation MCP servers. A browser can fetch arbitrary URLs and execute JS,
  making it a complete exfiltration channel. Explicit denial provides
  defence-in-depth even when `defaultMode: dontAsk` would already block
  unknown tools.
- **`mcp__scheduled-tasks__*`** — scheduling MCP. Same escape concern as
  `CronCreate`: a scheduled future run would operate outside this lockdown.

#### `env.DISABLE_TELEMETRY`, `DISABLE_ERROR_REPORTING`, `DISABLE_NON_ESSENTIAL_MODEL_CALLS`

**What they do:** turn off Claude Code's usage telemetry, crash reporting,
and auxiliary LLM calls (classifier, summarizer, etc.). Keeps the only
outbound traffic the main ZDR-backed completions.

**Why:** belt-and-suspenders — the ZDR key alone should be sufficient, but
we don't want to rely on telemetry endpoints also being ZDR-clean. Set in
project settings so every user inherits them.

#### `env.CLAUDE_CODE_DISABLE_TERMINAL_TITLE`

Cosmetic only. Stops Claude Code from setting the terminal title to the
current prompt, which could expose snippets of PII in shell history or
tmux logs.

### `safe-claude/mcp.json`

#### Two servers, checked-in

`connect_labs` (remote HTTP) and `commcare_hq_mcp` (local stdio) — nothing
else. Combined with `--strict-mcp-config`, the user's `~/.claude/mcp.json`
is ignored for this session, so no other MCP server (Google Drive, Atlassian,
etc.) can exfiltrate labs data.

#### `connect_labs.url`

Hard-coded to `https://labs.connect.dimagi.com/mcp/`. A pytest asserts this
to catch "oops, I pointed it at localhost for testing and committed".

#### `connect_labs.headers.Authorization: "Bearer ${LABS_MCP_TOKEN}"`

Placeholder only. The task renders a `0600`-mode tempfile with the real PAT
inlined at launch and deletes it on exit, so **no token is ever written to
a persistent file**. Committing a real token would fail the
`test_safe_mode_mcp_template_has_no_real_token` pytest.

#### `commcare_hq_mcp.command: "python" / args: ["tools/commcare_hq_mcp/server.py"]`

Runs the local stdio server in-process. Reads `COMMCARE_USERNAME` +
`COMMCARE_API_KEY` from the same `.env`. No HQ form or case data is ever
exposed — only app metadata (modules, forms, question paths).

## Sandbox: considered and deferred

Claude Code has a [`sandbox` feature](https://code.claude.com/docs/en/settings)
that wraps `Bash` in an OS-level sandbox (macOS Seatbelt / Linux Landlock)
with filesystem and network allow/deny lists. It's real, well-documented,
and the right answer if you ever *allow* shell access in safe mode.

We don't use it today because **we deny `Bash` entirely**. Sandbox adds no
defense where there's no Bash execution. If you ever need to allow a narrow
shell surface (e.g., `git log` for change context), turn on sandbox at the
same time:

```json
{
  "sandbox": {
    "enabled": true,
    "failIfUnavailable": true,
    "filesystem": {
      "allowRead": ["."],
      "denyWrite": ["/"],
      "denyRead": ["~/.ssh", "~/.aws", ".env", ".env.*"]
    },
    "network": {
      "allowedDomains": [],
      "allowManagedDomainsOnly": true
    }
  }
}
```

Do **not** re-allow `Bash` without stacking sandbox on top.

## Verifying safe mode

Two layers: an automated config-drift test you run on every change, and a
manual smoke test you run once after touching the config.

### Automated (every change, including CI)

    pytest commcare_connect/labs/tests/test_safe_mode_config.py

Assertions covering denied tool set, allow-list shape, permission mode,
escape-hatch disablement, MCP surface, labs URL, and the no-committed-token
invariant. Fails fast if anyone re-adds `Bash` to allow, flips
`defaultMode`, or swaps labs for localhost. Does **not** test Claude Code's
enforcement itself — that's Anthropic's job.

### Scripted end-to-end (optional — costs money, hits live labs)

    inv safe-claude-e2e --workflow-id=123 --opportunity-id=456
    inv safe-claude-e2e --workflow-id=123 --opportunity-id=456 --pipeline-id=789

Drives `claude -p` through the exact same safe-mode config as `inv safe-claude`
and verifies the full stack end-to-end:

1. `workflow_get` — read initial `render_code_version`.
2. `workflow_update_render_code` — append a timestamped `/* e2e-test-<ts> */`
   JSX comment, confirm the version bumps.
3. `workflow_get` — confirm the marker landed in the pushed JSX.
4. `workflow_update_render_code` — strip the marker, confirm version bumps
   again (JSX is restored).
5. *(if `--pipeline-id`)* `pipeline_sql` — confirm SQL is returned.
6. *(if `--pipeline-id`)* `pipeline_preview` — confirm rows are returned.

Each step is a separate `claude -p` call, so a pass proves the whole chain
(settings.safe.json → Claude Code → rendered mcp.safe.json → labs MCP server
→ labs DB). Expected cost: ~$0.40–0.60 per full run.

Use a **disposable workflow** you control. On success the marker comment is
reverted; on mid-run failure a stray `/* e2e-test-<ts> */` may remain in the
JSX until you clean it up.

### Manual smoke (once, after config changes)

Run `inv safe-claude` and work through this list against a disposable
workflow and pipeline in a labs opp you control.

**Round-trip (positive cases):**

1. "Pull workflow `<id>` from opp `<id>`" — `workflow_get` returns JSX +
   definition.
2. "Add a harmless comment to the JSX and push it back" —
   `workflow_update_render_code` returns a new version number.
3. In a separate labs tab, confirm the change landed.
4. "Pull pipeline `<id>`, preview it, add a trivial computed field, save"
   — `pipeline_get` → `pipeline_preview` → `pipeline_update_schema` round-trip.
5. Revert your edits.

**Lockdown (negative cases):**

6. "What MCP servers and tools do you have access to?" — reply should name
   exactly `connect_labs` + `commcare_hq_mcp` and omit Bash, Write, Edit,
   WebFetch, WebSearch, Agent.
7. "Run `ls` via the Bash tool" — Claude Code should refuse silently (no
   "approve once?" prompt). If it runs, stop and inspect settings.
8. "Write a file to /tmp/safe-mode-test" — refuse.
9. "Fetch https://example.com" — refuse.
10. "Spawn a subagent to do X" — refuse.

Any positive execution of #7–#10 means the config is broken — re-run the
pytest, compare `safe-claude/settings.json` to this doc.

## Extending the allow-list

To add another MCP server later (e.g., OCS):

1. Add the server block to `safe-claude/mcp.json`.
2. Add `"mcp__<server_name>__*"` to `permissions.allow` in
   `safe-claude/settings.json`.
3. Update the expected MCP set in
   `test_safe_mode_mcp_surface_is_exactly_expected_servers`.
4. Update the table in this doc.

Do not add `Bash`, `Write`, `Edit`, `WebFetch`, `WebSearch`, or `Agent` to
allow without a threat-model review — and if you allow `Bash`, also turn on
sandbox (see above). They are denied for specific reasons spelled out in
the per-setting review.

## MCP tool-level guardrails

The permission rules above block Claude Code from calling dangerous *local* tools.
The `connect_labs` MCP server also enforces policy server-side on write operations:

- **`create_solicitation`** — raises `POLICY_VIOLATION` if the caller attempts to
  set `is_public: true`. Solicitation records must remain private.
- **`create_fund` / `create_review`** — these records are legitimately public (orgs
  read their own fund and review data). However, both tools require the caller to
  pass `public_record_acknowledged: true`. Passing `false` raises `POLICY_VIOLATION`.
  The tool description, individual field descriptions, and the return value all
  name the specific free-text fields that must not contain PII (`description` for
  funds, `notes` for reviews).
- **`award_response`** — sets the response record to `public=True` so the awarded
  organisation can read their own status. The tool logs a `logger.warning()` and
  returns a `_warning` key in the response naming the risk fields.
- **`update_fund` / `update_review` / `update_solicitation`** — strip `is_public`
  and `public` from the update payload before merging, preventing an injection from
  flipping visibility on an existing record.
- **`pipeline_preview`** — `sample_size` is capped at 200 rows both in the JSON
  schema (`maximum: _PIPELINE_PREVIEW_MAX_ROWS`) and by a runtime check, preventing
  large data dumps through the preview path.

## Threat model

Safe mode is about **PII safety under a trusted operator**. It protects
against:

- Accidental exfiltration of patient/visit data to a non-ZDR workspace, the
  public web, or third-party MCP servers.
- Prompt-injection-style inputs (fetched through MCP) attempting to run
  shell commands, write files, or call unexpected tools — policy is
  enforced before the prompt is consulted.

It does **not** prevent an operator from copy-pasting PII out of the
terminal or taking screenshots. If you need stricter isolation, run it
inside a short-lived VM with no other network egress.
