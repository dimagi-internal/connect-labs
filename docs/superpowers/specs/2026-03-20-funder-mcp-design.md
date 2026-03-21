# Funder MCP Interface Design

## Summary

Extend the existing Connect Labs MCP server (`tools/commcare_mcp/server.py`) with fund management, review/scoring, award, and Google Sheets tools. These tools serve both local Claude Code sessions and in-app AI agents on the server, using the same code paths and prompts.

## Motivation

Connect Labs tracks funders (ECF, Founders Pledge, GiveWell, Gavi), their funds, and how those funds flow to programs/opportunities via solicitations and awards. Today, fund records exist but can only be managed through Django views. Exposing the full funder workflow via MCP enables:

- Interactive setup of funder-to-program relationships from source spreadsheets
- AI agents that understand the full funding pipeline (solicitation → response → review → award → allocation)
- Consistent tooling between local dev (Claude Code) and server-side AI

## Architecture

### Single MCP Server

All tools live in the existing `tools/commcare_mcp/` MCP server. No separate servers — funds, solicitations, reviews, Google Sheets, and CommCare HQ tools are all in one place. This keeps auth simple and allows tools to reference each other naturally (e.g., awarding a response triggers a fund allocation).

### Two-layer tool architecture

The codebase has two layers of tools that serve different runtimes:

1. **`tools/commcare_mcp/*.py`** — Async httpx functions for the standalone MCP server (stdio subprocess, no Django). Used by Claude Code locally and MCP clients.
2. **`commcare_connect/*/mcp_tools.py`** — Django-side wrappers that call `data_access.py` classes. Used by in-app AI agents that have access to the Django request/session.

For funds, `FunderDashboardDataAccess` already exists in `commcare_connect/funder_dashboard/data_access.py`. The MCP server cannot import Django code, so `fund_tools.py` reimplements the same operations with raw httpx. This duplication is the same pattern used for solicitations (`solicitation_tools.py` vs `solicitations/mcp_tools.py`).

Populating `funder_dashboard/mcp_tools.py` (Django-side wrappers) is out of scope — it can be done when in-app AI agents need fund tools.

### New Files

| File | Purpose |
|------|---------|
| `tools/commcare_mcp/fund_tools.py` | Async httpx functions for fund CRUD + allocations |
| `tools/commcare_mcp/review_tools.py` | Async httpx functions for review CRUD |
| `tools/commcare_mcp/google_auth.py` | Google OAuth CLI + token caching (already created) |
| `tools/commcare_mcp/google_tools.py` | Google Sheets/Drive read functions (already created) |

### Modified Files

| File | Change |
|------|--------|
| `tools/commcare_mcp/server.py` | Register fund, review, award, and Google tools; update instructions string |
| `tools/commcare_mcp/solicitation_tools.py` | Add `award_response` function |

## Tool Inventory

### Fund Tools (`fund_tools.py`)

All functions are async, use httpx, and call the Labs Record API at `/export/labs_record/`. Funds use `type="fund"` and `experiment=org_id`.

| Tool | Args | Returns |
|------|------|---------|
| `list_funds` | `organization_id` | List of fund dicts |
| `get_fund` | `fund_id` | Single fund dict with allocations |
| `create_fund` | `name, total_budget, currency, organization_id, program_ids[], delivery_types[], status` | Created fund dict |
| `update_fund` | `fund_id, data_json` | Updated fund dict (merge semantics) |
| `add_fund_allocation` | `fund_id, allocation_json` | Updated fund dict |
| `remove_fund_allocation` | `fund_id, index` | Updated fund dict |

Fund record data shape:
```json
{
  "name": "GiveWell CHC Fund",
  "description": "...",
  "total_budget": 500000,
  "currency": "USD",
  "org_id": "42",
  "program_ids": [25, 30],
  "delivery_types": ["CHC", "MBW"],
  "status": "active",
  "allocations": [
    {
      "program_id": "25",
      "program_name": "Nigeria CHC",
      "amount": 100000,
      "type": "award",
      "solicitation_id": 123,
      "response_id": 456,
      "org_id": "99",
      "org_name": "PPFN",
      "notes": "Award from CHC Scale-Up RFP"
    }
  ]
}
```

### Review Tools (`review_tools.py`)

Reviews use `type="solicitation_review"` and `labs_record_id=response_id`.

| Tool | Args | Returns |
|------|------|---------|
| `list_reviews` | `response_id` | List of review dicts |
| `get_review` | `review_id` | Single review dict |
| `create_review` | `response_id, llo_entity_id, score, recommendation, notes, criteria_scores_json, reviewer_username` | Created review dict |
| `update_review` | `review_id, data_json` | Updated review dict |

### Award Tool (in `solicitation_tools.py`)

| Tool | Args | Returns |
|------|------|---------|
| `award_response` | `response_id, reward_budget, org_id, fund_id (optional)` | Updated response dict |

When `fund_id` is provided, auto-creates a fund allocation. The multi-step flow:

```
1. GET response by response_id (to get current data + llo_entity_id)
2. POST update response: merge {status: "awarded", reward_budget, org_id} into existing data
3. If fund_id provided:
   a. GET solicitation by response's solicitation_id (to get title for allocation notes)
   b. POST add_fund_allocation to fund_id with:
      {amount: reward_budget, type: "award", solicitation_id, response_id, org_id, org_name, notes}
4. Return updated response dict
```

The `fund_id` is an explicit argument (not looked up from the solicitation) so callers have full control.

### Google Tools (already implemented)

| Tool | Args | Returns |
|------|------|---------|
| `read_google_sheet` | `url, tab_name, cell_range` | Headers + rows as dicts |
| `list_sheet_tabs` | `url` | List of tab names/gids |

## Auth Model

| Context | Connect API | Google API |
|---------|------------|------------|
| Local (Claude Code) | CLI token: `~/.commcare-connect/token.json` | CLI token: `~/.connect-labs/google-token.json` |
| Server (web app) | `request.session["labs_oauth"]` | `request.session["google_oauth"]` (future) |
| Server (AI agents) | User's session token passed through | User's Google token passed through (future) |

User OAuth only. No service accounts for now (can add later).

Google OAuth uses the same GCP project credentials:
- Client ID: stored in 1Password (`Connect Labs .env / GOOGLE_OAUTH_CLIENT_ID`)
- Client Secret: stored in 1Password (`Connect Labs .env / GOOGLE_OAUTH_CLIENT_SECRET`)
- Scopes: `spreadsheets.readonly`, `drive.readonly`
- Token cached at `~/.connect-labs/google-token.json` (local) or session (server)

## Implementation Pattern

All new tool files follow the existing `solicitation_tools.py` pattern:

```python
# fund_tools.py
import httpx
from connect_client import CONNECT_URL, HTTP_TIMEOUT, _get_connect_token

LABS_RECORD_URL = f"{CONNECT_URL.rstrip('/')}/export/labs_record/"

def _headers() -> dict[str, str]:
    token = _get_connect_token()
    return {"Authorization": f"Bearer {token}"}

async def list_funds(organization_id: str) -> list[dict]:
    params = {"type": "fund", "experiment": organization_id}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.get(LABS_RECORD_URL, params=params, headers=_headers())
        resp.raise_for_status()
        return [_serialize_record(r) for r in resp.json()]
```

Server.py registers each tool with `@mcp.tool()` wrappers that handle errors and provide docstrings with arg descriptions.

## Funder Data from Spreadsheet

Source: [Opp Enrichment tab](https://docs.google.com/spreadsheets/d/1_3JtvMNmZvZcyYgMSsyVOKxwxcOIP2wpEG-dsyS_6D0/edit?gid=1845169851)

Distinct funders and their opportunity counts:
- **ECF** — 5 opps (KMC, CHC)
- **Founders Pledge** — 43 opps (CHC, ECD, MBW, HHS, MH, READERS, WELLME)
- **GiveWell** — 34 opps (CHC, MBW)
- **Gavi** — 2 opps (CHC)
- **Other Funder** — 42 opps (IVP, various)

These will be created as fund records via MCP tools after implementation, linking each funder to their opportunities via the `program_ids` and `allocations` fields.

## Scope

### In scope
- Fund, review, award MCP tools in `tools/commcare_mcp/`
- Google Sheets MCP tools (done)
- Google OAuth CLI for local dev (done)
- Registration of all tools in `server.py`

### Out of scope (future)
- Web UI dashboards for funders (next phase)
- Google OAuth flow in Django web app (`request.session["google_oauth"]`)
- Service account auth for Google
- Fund tools in the solicitation AI agent
- Google Docs/Drive write access (read-only for now)
