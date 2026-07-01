# CommCare HQ MCP Server — Design

**Date:** 2026-03-07
**Status:** Approved

## Overview

Always-on MCP server for Claude Code sessions that provides CommCare application structure context. When building or debugging workflow pipeline schemas, Claude can query form question paths, case types, and module structure instead of guessing.

**Primary use case:** Fix the KMC template's empty fields by understanding the actual CommCare form structure, and prevent the same issue in future workflow templates.

## Architecture

```
Claude Code  ──stdio──>  commcare_mcp server  ──HTTP──>  CommCare HQ API
                              │                            (/api/v0.5/application/)
                              ├─ reads env vars for auth
                              ├─ caches app definitions in memory
                              └─ serves 3 static reference resources
```

**Location:** `tools/commcare_mcp/` in the Connect repo
**Stack:** Python, `mcp` SDK (FastMCP), `httpx` for HTTP, stdio transport
**Auth:** CommCare HQ API key via env vars (`COMMCARE_HQ_API_KEY`, `COMMCARE_HQ_USER_EMAIL`, `COMMCARE_HQ_DOMAIN`)

## MCP Tools (4)

### `list_apps`
- **Input:** `domain` (optional, defaults to env var)
- **Output:** List of apps with name, ID, module count, form count
- **Use:** "What apps exist on this domain?"

### `get_app_structure`
- **Input:** `domain`, `app_id`
- **Output:** Tree: modules → forms (with xmlns, case_type) → case types
- **Use:** "Show me the KMC app structure"

### `get_form_questions`
- **Input:** `domain`, `app_id`, `xmlns`
- **Output:** Question tree with IDs, types, labels, constraints, groups/repeats
- **Use:** "What fields does the KMC visit form have?"

### `get_form_json_paths`
- **Input:** `domain`, `app_id`, `xmlns`
- **Output:** Flat list mapping each question to its form submission JSON path
- **Use:** "What path should I use in PIPELINE_SCHEMAS for the weight field?"
- **Key logic:** Maps question paths to JSON paths:
  - `/data/weight` → `form.weight`
  - `/data/child_info/birth_weight` → `form.child_info.birth_weight`
  - `/data/case/update/child_weight` → `form.case.update.child_weight`
  - Groups create nested objects, repeats create arrays

## MCP Resources (3)

### `commcare://app-schema`
- **Source:** CommCare Forge's `compact-json-schema.md`
- **Content:** Question type taxonomy (20 types), case property mapping rules (`case_properties`, `case_preload`), group/repeat nesting, 24 reserved property names, validation rules
- **Why:** Provides the vocabulary for understanding CommCare app structure

### `commcare://xml-reference`
- **Source:** CommCare Forge's `commcare-reference.md`
- **Content:** XForm/Suite/Case XML structure — bind types, case operations (create/update/close/index), session datums, detail definitions
- **Why:** Explains the upstream XML that produces the JSON structures we see in form submissions

### `commcare://data-patterns`
- **Source:** New doc, distilled from Scout's loader code
- **Content:** How form submission JSON actually looks at query time:
  - Form JSON structure (`form.` prefix, nested groups, repeat arrays)
  - Case block nesting (`form.case.@case_id`, `form.group.case.@case_id`)
  - Question path → JSON path mapping rules
  - Common pitfalls (Python repr in form_json, reserved properties, `@` attributes)
- **Why:** The operational reality of working with CommCare data

## Caching

App definitions can be 5-10MB. Cache in memory after first fetch, keyed by `(domain, app_id)`. Invalidated on server restart. No persistent storage.

## Configuration

```json
// .claude/mcp.json
{
  "mcpServers": {
    "commcare-hq": {
      "command": "python",
      "args": ["tools/commcare_mcp/server.py"],
      "env": {
        "COMMCARE_HQ_DOMAIN": "your-domain",
        "COMMCARE_HQ_API_KEY": "user@example.com:your-api-key",
        "COMMCARE_HQ_URL": "https://www.commcarehq.org"
      }
    }
  }
}
```

## What It Does NOT Do

- No real data access (no form submissions, no case data, no PII)
- No writes to CommCare
- No materialization or storage
- No Connect API access (that's handled by the existing labs infrastructure)

## File Structure

```
tools/commcare_mcp/
├── server.py              # MCP server entry point (FastMCP, tool definitions)
├── hq_client.py           # CommCare HQ API client (httpx, auth, caching)
├── extractors.py          # App structure extraction, question path mapping
├── resources/
│   ├── app_schema.md      # CommCare Forge compact-json-schema (bundled)
│   ├── xml_reference.md   # CommCare Forge XForm/Suite/Case reference (bundled)
│   └── data_patterns.md   # Scout-derived form JSON patterns (new)
└── requirements.txt       # mcp, httpx
```

## Key References

- **Scout** (`../scout/mcp_server/loaders/commcare_metadata.py`): App structure extraction from HQ API — battle-tested logic for walking modules/forms/case_types
- **CommCare Forge** (`kcowger/commcare-forge` PR #3): Reference docs for CommCare concepts, question types, case property patterns
- **Existing HQ API code** (`connect_labs/utils/commcarehq_api.py`): Already has `_get_commcare_app_json()` calling `/api/v0.5/application/`
- **Existing HQ OAuth** (`connect_labs/labs/integrations/commcare/`): Full OAuth flow if we want to upgrade from API key auth later

## Design Decisions

1. **API key auth (not OAuth)** — Simpler for an always-on MCP server. No browser flow needed. Can upgrade to OAuth later if needed.
2. **Stdio transport** — Standard for Claude Code MCP servers. No HTTP port management.
3. **In-repo location** — Lives in `tools/` rather than a separate package. Simple, discoverable, versioned with the project.
4. **Static resource bundling** — Reference docs are copied into the repo, not fetched at runtime. They're stable documents that change rarely.
5. **Python** — Same stack as Connect. Can reuse patterns from existing HQ API code. `mcp` Python SDK is sufficient for our needs.
