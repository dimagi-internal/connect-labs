# Connect MCP

Connect Labs gives technical program staff a way to edit workflows using Claude Code (an AI assistant) from the command line — without writing code themselves.

!!! note "Who this is for"
    This feature is for program administrators and technical staff who are comfortable working in a terminal. If you just want to use the AI assistant inside the Labs browser app, see [AI Features](ai-features.md) instead.

---

## What Is This For?

Normally, editing a workflow's display logic or data fields requires a developer to modify code. With the Connect MCP (Model Context Protocol), you can describe changes in plain English and Claude Code makes the edits for you.

**Claude Code** is the AI assistant CLI you run in your terminal. **MCP** (Model Context Protocol) is the server it connects to — hosted inside Connect Labs — that gives Claude Code the tools to read and update workflows. You use Claude Code; it uses the MCP behind the scenes.

<!-- prettier-ignore -->
> _"Add a column showing how many weeks since the last visit"_
> _"Change the status colors so 'Overdue' shows in red"_
> _"Remove the RUTF field from the table — it's not relevant for this program"_

Claude reads the workflow's current definition, makes the change, and pushes it back to Labs — all from your terminal.

---

## Prerequisites

| You need | For |
| --- | --- |
| A **Labs login** (the account you use at [labs.connect.dimagi.com](https://labs.connect.dimagi.com)) | Everything |
| **Claude Code** (`npm install -g @anthropic-ai/claude-code`), or Claude desktop / claude.ai | Everything |
| A clone of the connect-labs repository (`git clone https://github.com/dimagi-internal/connect-labs.git`) | The `/workflow-author` skill and Safe Mode |
| The 1Password CLI (`op`), with access to the **Employee** and **AI-Agents** vaults | Safe Mode only |

Ask in **#engineering-connect** if you're unsure about any of these.

!!! note "You don't need to run Labs locally"
    For workflow editing you do **not** need to run the Django app locally. The Labs MCP server is hosted on Labs itself; Claude pushes workflow changes directly to Labs prod, and you verify the result in your browser. Run locally only if you are modifying the core Connect Labs application code itself.

---

## First-Time Setup

### 1. Connect Claude to Labs

=== "Claude Code"

    ```bash
    claude mcp add --transport http connect_labs https://labs.connect.dimagi.com/mcp/
    ```

    Then type `/mcp`, choose `connect_labs` and sign in with CommCare Connect when the browser opens. There is no token to copy.

=== "Claude desktop or claude.ai"

    Open **Settings → Connectors**, choose **Add custom connector**, and add `https://labs.connect.dimagi.com/mcp/`. Click **Connect** and sign in with CommCare Connect.

Claude acts as you, with your Connect permissions. To see or disconnect the apps you have signed in, visit [labs.connect.dimagi.com/labs/mcp/tokens/](https://labs.connect.dimagi.com/labs/mcp/tokens/).

### 2. (Safe Mode only) Register a Labs token

[Safe Mode](connect-safe-mode.md) cannot use the browser sign-in — it needs a Personal Access Token. From **inside your connect-labs checkout**, start Claude Code and run:

```
/labs-token-setup
```

When asked, choose **Production labs environment**, then approve the token in the browser. Fully quit and restart Claude Code afterwards. The token lasts 90 days by default; run the skill again to replace it.

### 3. (Safe Mode only) Fetch the CommCare HQ credentials

```bash
op signin --account dimagi
op inject -f -i .env.tpl -o .env
```

The `.env` holds the CommCare HQ credentials Safe Mode's read-only app-structure tools use.

---

## Editing Workflows

!!! tip "Working with real program data?"
    Launch Claude via [Safe Mode](connect-safe-mode.md) before running `/workflow-author` — it blocks data-exfiltration channels while keeping workflow edits available.

Use the MCP-powered workflow skill:

```
/workflow-author
```

Then describe what you want in plain English. Claude will:

1. Pull the current workflow definition from Labs
2. Show you what it plans to change
3. Apply the change and push it back
4. Confirm the update was successful

To verify your change: open the workflow in your browser at [labs.connect.dimagi.com](https://labs.connect.dimagi.com).

### Iteration loop and deployment bar

Workflow definitions are user-generated content stored in Connect prod — updating them requires no pull request and no code review. Keep a low bar for pushing changes: if something looks wrong, describe the fix and let Claude push again. To revert, tell Claude what to undo and it will push a corrected version. If you can't resolve an issue after a few iterations, ask in **#connect-labs**.

The power of this loop is: describe change → Claude pushes → reload browser → verify → repeat. Get comfortable with that cadence rather than doing a lot of intermediate work to validate locally first.

**Note:** changes to the MCP server itself (the Labs code that powers these tools) _do_ require a code deploy. But for all workflow edits, the MCP push is sufficient.

### Template authoring (regular Claude session only)

Safe Mode is for editing **live workflow instances**. If you are authoring or updating a **seed template** (a `.py` file in the repository that other workflows are cloned from), you need a regular Claude Code session instead — Safe Mode blocks the file writes that template authoring requires.

In a regular session, you can use `workflow_sync_from_template_file` to push a local `.py` file straight to a live preview workflow without a full redeploy. The full loop is in the "Two iteration loops" section of the [`workflow-author` skill](https://github.com/dimagi-internal/connect-labs/blob/main/.claude/skills/workflow-author/SKILL.md). It refuses a workflow that follows the deployed template.

---

## More Information

- **[Reports with Claude](reports-with-claude.md)** — plain-English guide to changing reports, pipelines and indicator definitions through the MCP
- **[MCP_SETUP.md](https://github.com/dimagi-internal/connect-labs/blob/main/docs/MCP_SETUP.md)** — Labs MCP server and token details
- For security guardrails when working with real program data, see [Safe Mode](connect-safe-mode.md)
- For help, post in **#connect-labs** on Slack
