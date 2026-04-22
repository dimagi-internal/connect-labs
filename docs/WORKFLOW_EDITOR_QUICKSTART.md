# Editing Labs Workflows with Safe Claude — Quickstart

Use Claude from the command line to edit labs workflows safely. No code
knowledge required.

## Prereqs

`git`, `python@3.11`, `node` + `@anthropic-ai/claude-code`, Python `invoke`,
and the 1Password CLI (`op`). Dimagi 1Password (Employee + AI-Agents vaults),
Labs login, GitHub access to `dimagi/connect-labs`. Ask your AI to help
install anything missing.

### Install the 1Password CLI

`inv safe-claude` fetches credentials from 1Password at launch, so `op` must
be installed and signed in.

**macOS:**

```bash
brew install 1password-cli
```

**WSL (Windows Subsystem for Linux):**

```bash
# Add the 1Password apt repository
curl -sS https://downloads.1password.com/linux/keys/1password.asc | \
  sudo gpg --dearmor --output /usr/share/keyrings/1password-archive-keyring.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/1password-archive-keyring.gpg] https://downloads.1password.com/linux/debian/$(dpkg --print-architecture) stable main" | \
  sudo tee /etc/apt/sources.list.d/1password.list

sudo mkdir -p /etc/debsig/policies/AC2D62742012EA22/
curl -sS https://downloads.1password.com/linux/debian/debsig/1password.pol | \
  sudo tee /etc/debsig/policies/AC2D62742012EA22/1password.pol
sudo mkdir -p /usr/share/debsig/keyrings/AC2D62742012EA22
curl -sS https://downloads.1password.com/linux/keys/1password.asc | \
  sudo gpg --dearmor --output /usr/share/debsig/keyrings/AC2D62742012EA22/debsig.gpg

sudo apt update && sudo apt install 1password-cli
```

Then verify and sign in:

```bash
op --version              # should print 2.x.x
op signin --account dimagi
```

## Setup (once)

Clone the repo and seed your `.env` from 1Password:

```bash
git clone https://github.com/dimagi/connect-labs.git && cd connect-labs
op inject -f -i .env.tpl -o .env && chmod 600 .env
```

### Register your Labs token

A Personal Access Token (PAT) is a password substitute that lets the
`connect_labs` MCP server verify your identity without your login
credentials. Open a normal Claude Code session (from any folder) and:

1. Run `/labs-token-setup`.
2. When Claude prompts, answer **Production labs environment**.
3. Claude Code prints a URL to the terminal. Open it in your browser and
   approve the token. On WSL, copy-paste the printed URL into your Windows
   browser — the Linux-side auto-open can't reach it, but WSL2's localhost
   forwarding still delivers the callback. The token is written to
   `~/.claude.json`.
4. `/exit`, close the terminal, open a new one, restart Claude Code, and run
   `/mcp`. You should see `connect_labs` with `✓ Connected`.

## Running

Pull the latest before each session:

```bash
cd connect-labs
git pull origin main
```

Activate the Python venv so `inv` resolves to this repo's task definitions
(example paths — adjust to where you created yours):

```bash
source ~/venvs/commcare-labs/bin/activate   # WSL / Linux
source .venv/bin/activate                   # macOS
```

Launch safe-mode Claude:

```bash
inv safe-claude --auth=api-key
```

`--auth` is required every run — pick `api-key` (Anthropic ZDR key) or
`vertex` (Google Vertex AI). There's no default on purpose: you should
always know which governed endpoint your PII is about to route through.

## Troubleshooting

| Error | Fix |
|---|---|
| `No connect_labs PAT found` | Run `/labs-token-setup` in a regular Claude Code session |
| `op` not on PATH or "sign in" errors | Install the 1Password CLI (see above), then `op signin --account dimagi` |
| `Fetched value does not look like an Anthropic API key` | 1Password item is wrong — ask in **#engineering-connect** |
| "Workflow not found" / 403 | Check the IDs; confirm you can open it in the Labs browser |
| Claude says "I can't edit files" | That's correct — prompt it to use `connect_labs` MCP tools instead |

## What safe-claude can and can't do

Can: read/edit workflows + pipelines, read/create solicitations/funds/reviews,
read HQ app structure (no form data), read this repo's docs.

Can't: run shell commands, edit local files, fetch URLs, web search, spawn
sub-agents, or talk to any MCP server besides `connect_labs` and
`commcare_hq_mcp`.

## More

- **[SAFE_MODE.md](SAFE_MODE.md)** — full design + security model (read this
  before changing anything in `safe-claude/settings.json`).
- **[MCP_SETUP.md](MCP_SETUP.md)** — Labs MCP server + token details.
- **[CLAUDE.md](../CLAUDE.md)** — full architecture reference.
