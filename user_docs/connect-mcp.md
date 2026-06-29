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

Before you start, you'll need these tools installed:

| Tool                 | How to get it                                                                        |
| -------------------- | ------------------------------------------------------------------------------------ |
| `git`                | [git-scm.com](https://git-scm.com)                                                   |
| Python 3.11+         | [python.org](https://www.python.org)                                                 |
| Node.js              | [nodejs.org](https://nodejs.org)                                                     |
| Claude Code CLI      | `npm install -g @anthropic-ai/claude-code`                                           |
| 1Password CLI (`op`) | [1password.com/downloads/command-line](https://1password.com/downloads/command-line) |

You'll also need:

- A Dimagi 1Password account with access to the **AI-Agents** vault
- A **Labs login** (same account you use at [labs.connect.dimagi.com](https://labs.connect.dimagi.com))
- Access to the connect-labs GitHub repository

Ask in **#engineering-connect** if you're unsure about any of these.

!!! note "You don't need to run Labs locally"
    For workflow editing, cloning the repository is enough — you do **not** need to run the Django app locally. Even a local instance fetches all data from Connect prod, so there is no isolation benefit. Claude Code pushes workflow changes directly to Labs prod, and you verify the result in your browser. Run locally only if you are modifying the core Connect Labs application code itself.

---

## First-Time Setup

### 1. Install 1Password CLI and sign in

=== "macOS"

    ```bash
    brew install 1password-cli
    op signin --account dimagi
    ```

=== "Windows (WSL)"

    ```bash
    curl -sS https://downloads.1password.com/linux/keys/1password.asc | \
      sudo gpg --dearmor --output /usr/share/keyrings/1password-archive-keyring.gpg
    sudo apt update && sudo apt install 1password-cli
    op signin --account dimagi
    ```

### 2. Clone the repo and set up credentials

```bash
git clone https://github.com/dimagi-internal/connect-labs.git
cd connect-labs
op inject -f -i .env.tpl -o .env
```

### 3. Set up your Labs token

Your Labs token lets Claude Code talk to the Labs MCP server securely. Open a normal Claude Code session (in any folder) and run:

```
/labs-token-setup
```

Follow the prompts. When asked, choose **Production labs environment**. Claude will open a browser URL — approve the token there. This only needs to be done once (or when your token expires).

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

In a regular session, you can use `workflow_sync_from_template_file` to push a local `.py` file straight to a live preview workflow without a full redeploy. See [Deploy-Free Template Iteration](workflow-engine.md#deploy-free-template-iteration) for the full loop.

---

## More Information

- **[MCP_SETUP.md](https://github.com/dimagi-internal/connect-labs/blob/main/docs/MCP_SETUP.md)** — Labs MCP server and token details
- For security guardrails when working with real program data, see [Safe Mode](connect-safe-mode.md)
- For help, post in **#connect-labs** on Slack
