# Connect Safe Mode

Safe Mode adds security guardrails so that when AI has access to real program data, it cannot leak that data outside approved channels.

!!! note "Who this is for"
    This feature is for program administrators and technical staff who are comfortable working in a terminal. Complete [Connect MCP setup](connect-mcp.md) before using Safe Mode.

---

## What Is Safe Mode?

When Claude Code has access to real program data through the Connect MCP, there is a risk that it could accidentally send patient information to external services, write it to local files, or execute arbitrary commands. Safe Mode closes those channels. Claude can use the Labs MCP tools (acting as you), read-only CommCare HQ app-structure tools, and read local files — nothing else.

Model traffic in Safe Mode goes only to a governed endpoint — the Anthropic zero-data-retention (ZDR) API key (`--auth=api-key`) or Dimagi's Google Vertex project (`--auth=vertex`) — rather than a personal Claude account.

---

## Running Safe Mode

Pull the latest changes, then launch:

```bash
cd connect-labs
git pull origin main
source .venv/bin/activate
inv safe-claude --auth=api-key
```

The `--auth` flag is required every time — choose:

- `--auth=api-key` — Anthropic ZDR API key (recommended)
- `--auth=vertex` — Google Vertex AI endpoint

Once launched, use `/workflow-author` to describe workflow changes in plain English. See [Editing Workflows](connect-mcp.md#editing-workflows) for the full editing loop.

---

## What Safe Mode Protects Against

```mermaid
flowchart LR
    SM[Safe Mode\nLaunch] -->|Blocks| B[Bash / Shell]
    SM -->|Blocks| W[Web Fetch / Search]
    SM -->|Blocks| F[File Write]
    SM -->|Blocks| S[Subagent Spawn]
    SM -->|Allows| MCP[connect_labs MCP\nall Labs tools, as you]
    SM -->|Allows| HQ[CommCare HQ\nApp Structure only]
    SM -->|Routes through| ZDR[Governed AI endpoint\nZDR key or Vertex]
```

| Safe Mode blocks                    | Why                                                                |
| ----------------------------------- | ------------------------------------------------------------------ |
| Shell commands (`ls`, `curl`, etc.) | Can't execute arbitrary code or exfiltrate data via the filesystem |
| Web fetch / web search              | Can't send data to external URLs                                   |
| Writing local files                 | Can't dump patient data to disk                                    |
| Spawning sub-agents                 | Keeps the session audit trail linear and reviewable                |

**Safe Mode allows only:**

- The Labs MCP tools, acting as you — workflows and pipelines, but also everything else the Labs MCP offers (solicitations, funds, reviews, pages, synthetic data), **including edits and deletes**. Safe Mode limits where data can *go*, not what you can change in Labs.
- Reading CommCare HQ app structure (form definitions only — no patient data)
- Reading local files

---

## Troubleshooting

| Problem                           | Fix                                                                              |
| --------------------------------- | -------------------------------------------------------------------------------- |
| "No connect_labs PAT found"       | Safe Mode needs a Personal Access Token — signing in through `/mcp` is not enough. Run `/labs-token-setup` from inside your connect-labs checkout, then restart Claude Code. |
| `op` errors or sign-in failures   | Run `op signin --account dimagi` in your terminal                                |
| "Workflow not found" or 403 error | Check the workflow ID; confirm you can open it in Labs in the browser            |
| Claude says "I can't edit files"  | That's correct in Safe Mode — ask it to use the `connect_labs` MCP tools instead |

---

## More Information

- **[SAFE_MODE.md](https://github.com/dimagi-internal/connect-labs/blob/main/docs/SAFE_MODE.md)** — full technical design and security model
- For MCP setup and workflow editing, see [Connect MCP](connect-mcp.md)
- For help, post in **#connect-labs** on Slack
