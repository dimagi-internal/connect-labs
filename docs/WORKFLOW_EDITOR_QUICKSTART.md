# Editing Labs Workflows with Safe Claude — Quickstart

Use Claude from the command line to edit labs workflows safely. No code
knowledge required.

## Prereqs

`brew`, `python@3.11`, `git`, `1password-cli`, `node` + `@anthropic-ai/claude-code`,
and Python `invoke`. Dimagi 1Password (Employee + AI-Agents vaults), Labs
login, GitHub access to `dimagi/connect-labs`. Ask your AI to help install
anything missing.

## Setup (once)

```bash
git clone https://github.com/dimagi/connect-labs.git && cd connect-labs
op signin --account dimagi
op inject -f -i .env.tpl -o .env && chmod 600 .env
```

Then register your Labs token by opening Claude Code **from any other
folder** and running `/labs-token-setup` — browser approves, token saves,
`/exit`.

## Running

```bash
cd connect-labs
inv safe-claude --auth=api-key
```

`--auth` is required every run — pick `api-key` (Anthropic ZDR key) or
`vertex` (Google Vertex AI). There's no default on purpose: you should
always know which governed endpoint your PII is about to route through.

## Troubleshooting

| Error | Fix |
|---|---|
| `No connect_labs PAT found` | Run `/labs-token-setup` in a regular Claude Code session |
| `op` not on PATH or "sign in" errors | (mac) `brew install 1password-cli`; `op signin --account dimagi` |
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
