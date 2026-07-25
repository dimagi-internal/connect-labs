# CommCare HQ MCP Server — Implementation Plan

> **Status (2026-07-25 doc-regen):** SUPERSEDED — the labs MCP server lives at `connect_labs/mcp/` (the `tools/commcare_mcp/` location described here never became the home; HQ app tools live in `tools/commcare_hq_mcp/`).

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build an always-on MCP server that gives Claude Code access to CommCare application structure (form questions, case types, JSON paths) for building and debugging workflow pipeline schemas.

**Architecture:** Standalone Python MCP server using FastMCP (stdio transport). Queries CommCare HQ `/api/v0.5/application/` API for app definitions. Exposes 4 tools + 3 static reference resources. Caches app definitions in memory. Auth via API key in env vars.

**Tech Stack:** Python 3.13, `mcp` SDK (FastMCP), `httpx` for HTTP, bundled markdown resources from CommCare Forge + Scout.

---

### Task 1: Create directory structure and requirements

**Files:**
- Create: `tools/commcare_mcp/__init__.py`
- Create: `tools/commcare_mcp/requirements.txt`
- Create: `tools/commcare_mcp/resources/` (directory)

**Step 1: Create the directory and requirements file**

```
tools/commcare_mcp/
├── __init__.py           (empty)
├── requirements.txt
└── resources/
```

`tools/commcare_mcp/requirements.txt`:
```
mcp>=1.20.0
httpx>=0.27.0
```

**Step 2: Verify structure**

Run: `ls tools/commcare_mcp/`
Expected: `__init__.py  requirements.txt  resources/`

**Step 3: Commit**

```bash
git add tools/commcare_mcp/
git commit -m "feat: scaffold commcare MCP server directory"
```

---

### Task 2: Bundle reference resources

**Files:**
- Create: `tools/commcare_mcp/resources/app_schema.md`
- Create: `tools/commcare_mcp/resources/xml_reference.md`
- Create: `tools/commcare_mcp/resources/data_patterns.md`

**Step 1: Copy CommCare Forge's compact-json-schema.md**

Fetch from GitHub and save as `tools/commcare_mcp/resources/app_schema.md`:

```bash
gh api repos/kcowger/commcare-forge/contents/docs/compact-json-schema.md --jq '.content' | base64 -d > tools/commcare_mcp/resources/app_schema.md
```

**Step 2: Copy CommCare Forge's commcare-reference.md**

```bash
gh api repos/kcowger/commcare-forge/contents/docs/commcare-reference.md --jq '.content' | base64 -d > tools/commcare_mcp/resources/xml_reference.md
```

**Step 3: Write the data_patterns.md resource**

This is a new document distilled from Scout's loader code. Create `tools/commcare_mcp/resources/data_patterns.md`:

```markdown
# CommCare Form Data Patterns

How CommCare form submissions look as JSON when retrieved via API — the operational
reality needed for mapping pipeline schema field paths.

## Form Submission JSON Structure

When a CommCare form is submitted, the API returns:

```json
{
  "id": "form-uuid",
  "form": {
    "@xmlns": "http://openrosa.org/formdesigner/FORM-UUID",
    "@name": "Visit Form",
    "question_id": "value",
    "group_name": {
      "nested_question": "value"
    },
    "repeat_group": [
      {"item_question": "value1"},
      {"item_question": "value2"}
    ],
    "case": {
      "@case_id": "case-uuid",
      "update": {
        "property_name": "value"
      }
    },
    "meta": {
      "userID": "user-uuid",
      "timeStart": "2026-01-15T10:30:00Z",
      "timeEnd": "2026-01-15T10:35:00Z"
    }
  },
  "received_on": "2026-01-15T10:35:01Z",
  "app_id": "app-uuid"
}
```

## Question Path → JSON Path Mapping Rules

CommCare question IDs map to form submission JSON paths as follows:

| Question path in app definition | JSON path in form submission |
|--------------------------------|------------------------------|
| `/data/weight` | `form.weight` |
| `/data/child_info/birth_weight` (inside group) | `form.child_info.birth_weight` |
| `/data/visits/visit_date` (inside repeat) | `form.visits[].visit_date` |
| `/data/case/update/last_weight` (case property) | `form.case.update.last_weight` |
| `/data/case/@case_id` (case reference) | `form.case.@case_id` |
| `/data/meta/userID` (form metadata) | `form.meta.userID` |

**Rules:**
1. Strip the `/data/` prefix and replace with `form.`
2. Groups create nested objects: `/data/group/question` → `form.group.question`
3. Repeat groups create arrays: `/data/repeat/question` → `form.repeat[].question`
4. Case blocks appear at `form.case` (or deeper: `form.group.case`)
5. The `@` prefix on attributes is preserved: `@case_id`, `@xmlns`, `@name`
6. The `meta` block is always at `form.meta` with `userID`, `timeStart`, `timeEnd`, etc.

## Case Block Nesting

Case blocks can appear at ANY depth in the form JSON. They are identified by the
presence of `@case_id` in a dict:

```json
// Top-level case
"form": { "case": { "@case_id": "abc", "update": { "weight": "2500" } } }

// Nested in a group
"form": { "child_group": { "case": { "@case_id": "def", "create": { ... } } } }

// Inside a repeat group (one case per repeat entry)
"form": { "household_members": [
  { "case": { "@case_id": "ghi", "update": { ... } } },
  { "case": { "@case_id": "jkl", "update": { ... } } }
] }
```

## Common Field Patterns

### Weight/measurements
```
form.weight              → current weight (usually grams as string)
form.birth_weight        → birth weight
form.child_weight_visit  → weight at visit (alternative naming)
```

### GPS/Location
```
form.gps                 → "lat lon altitude accuracy" (space-separated string)
```

### Dates
```
form.visit_date          → "2026-01-15" (date string)
form.meta.timeStart      → "2026-01-15T10:30:00Z" (form open time)
form.meta.timeEnd        → "2026-01-15T10:35:00Z" (form submit time)
```

### Case identification
```
form.case.@case_id       → the case being updated
form.subcase_0.case.@case_id → child case (when creating sub-cases)
```

### Beneficiary/entity linking
```
form.case.@case_id          → the beneficiary case ID (most common)
form.case.index.parent      → parent case reference
```

## Common Pitfalls

1. **Field names are case-sensitive** — `form.Weight` ≠ `form.weight`
2. **Repeat groups become arrays** — even if there's only one entry
3. **Empty fields may be omitted** — check for key existence, don't assume all fields present
4. **Select multiple values are space-separated** — `"option1 option2 option3"`
5. **Numbers are strings** — weights, ages, etc. come as `"2500"` not `2500`
6. **GPS is a space-separated string** — `"0.3456 32.1234 1200 10"` (lat, lon, alt, accuracy)
7. **form_json from Connect visits** — may be Python repr format (`{'key': 'value'}` with single quotes) instead of valid JSON. Use `ast.literal_eval` as fallback.
8. **@-prefixed attributes** — `@case_id`, `@xmlns`, `@name` are XML attribute artifacts preserved in JSON
```

**Step 4: Verify all three resources exist**

Run: `ls tools/commcare_mcp/resources/`
Expected: `app_schema.md  data_patterns.md  xml_reference.md`

**Step 5: Commit**

```bash
git add tools/commcare_mcp/resources/
git commit -m "feat: bundle CommCare reference resources for MCP server"
```

---

### Task 3: Implement HQ API client with caching

**Files:**
- Create: `tools/commcare_mcp/hq_client.py`

**Step 1: Write the HQ client**

This is adapted from Scout's `commcare_base.py` + `commcare_metadata.py`. Uses `httpx` (async), API key auth, and in-memory caching.

Create `tools/commcare_mcp/hq_client.py`:

```python
"""CommCare HQ API client for fetching application definitions.

Auth: API key via env vars (COMMCARE_HQ_API_KEY format: "user@example.com:apikey123")
Caching: In-memory, keyed by (domain, app_id). Invalidated on server restart.
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

HQ_URL = os.environ.get("COMMCARE_HQ_URL", "https://www.commcarehq.org")
HQ_API_KEY = os.environ.get("COMMCARE_HQ_API_KEY", "")  # "user@email.com:apikey"
HQ_DOMAIN = os.environ.get("COMMCARE_HQ_DOMAIN", "")
HTTP_TIMEOUT = httpx.Timeout(connect=10, read=120, write=10, pool=10)

# In-memory cache: (domain, app_id) -> app definition dict
_app_cache: dict[tuple[str, str], dict] = {}
# domain -> list of app summaries
_app_list_cache: dict[str, list[dict]] = {}


def _auth_header() -> dict[str, str]:
    """Build Authorization header from env var."""
    if not HQ_API_KEY:
        raise ValueError(
            "COMMCARE_HQ_API_KEY not set. Format: 'user@example.com:your-api-key'"
        )
    return {"Authorization": f"ApiKey {HQ_API_KEY}"}


async def list_apps(domain: str | None = None) -> list[dict]:
    """Fetch all applications for a domain from CommCare HQ.

    Returns list of dicts with: id, name, version, module_count, form_count.
    Results are cached in memory.
    """
    domain = domain or HQ_DOMAIN
    if not domain:
        raise ValueError("No domain specified. Set COMMCARE_HQ_DOMAIN or pass domain parameter.")

    if domain in _app_list_cache:
        return _app_list_cache[domain]

    apps_raw = await _fetch_all_apps(domain)
    summaries = []
    for app in apps_raw:
        modules = app.get("modules", [])
        form_count = sum(len(m.get("forms", [])) for m in modules)
        summaries.append({
            "id": app.get("id", ""),
            "name": app.get("name", ""),
            "version": app.get("version", 0),
            "is_released": app.get("is_released", False),
            "module_count": len(modules),
            "form_count": form_count,
        })

    _app_list_cache[domain] = summaries
    return summaries


async def get_app(domain: str | None, app_id: str) -> dict:
    """Fetch a single application definition. Cached after first fetch."""
    domain = domain or HQ_DOMAIN
    if not domain:
        raise ValueError("No domain specified.")

    cache_key = (domain, app_id)
    if cache_key in _app_cache:
        return _app_cache[cache_key]

    # Fetch all apps and find the one we want
    apps = await _fetch_all_apps(domain)
    for app in apps:
        key = (domain, app.get("id", ""))
        _app_cache[key] = app

    if cache_key not in _app_cache:
        raise ValueError(f"App {app_id} not found in domain {domain}")

    return _app_cache[cache_key]


async def _fetch_all_apps(domain: str) -> list[dict]:
    """Fetch all application definitions from the HQ API with pagination."""
    url = f"{HQ_URL}/a/{domain}/api/v0.5/application/"
    params = {"limit": 100}
    apps: list[dict] = []

    async with httpx.AsyncClient(
        headers=_auth_header(),
        timeout=HTTP_TIMEOUT,
    ) as client:
        while url:
            resp = await client.get(url, params=params)
            if resp.status_code in (401, 403):
                raise PermissionError(
                    f"CommCare HQ auth failed for domain {domain}: HTTP {resp.status_code}. "
                    "Check COMMCARE_HQ_API_KEY."
                )
            resp.raise_for_status()
            data = resp.json()
            apps.extend(data.get("objects", []))
            url = data.get("next")
            params = {}  # next URL includes params

    logger.info("Fetched %d apps for domain %s", len(apps), domain)
    return apps


def clear_cache():
    """Clear all cached app definitions."""
    _app_cache.clear()
    _app_list_cache.clear()
```

**Step 2: Verify it imports cleanly**

Run: `cd tools/commcare_mcp && python -c "import hq_client; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add tools/commcare_mcp/hq_client.py
git commit -m "feat: add CommCare HQ API client with caching"
```

---

### Task 4: Implement extractors (app structure + JSON path mapping)

**Files:**
- Create: `tools/commcare_mcp/extractors.py`

**Step 1: Write the extractors module**

This is the core logic — extracts structured app info from raw HQ API responses and maps question paths to form JSON paths. Adapted from Scout's `_extract_case_types` and `_extract_form_definitions`.

Create `tools/commcare_mcp/extractors.py`:

```python
"""Extract structured app information from CommCare HQ API responses.

Functions:
- extract_app_structure: module → form → case type tree
- extract_form_questions: question tree with types and labels
- extract_form_json_paths: flat mapping of question → JSON path (for pipeline schemas)
- extract_case_types: all case types across an app's modules
"""

from __future__ import annotations

from typing import Any


def extract_app_structure(app: dict) -> dict:
    """Extract a clean app structure tree from a raw HQ app definition.

    Returns:
        {
            "app_id": str,
            "app_name": str,
            "modules": [
                {
                    "name": str,
                    "case_type": str,
                    "forms": [
                        {"name": str, "xmlns": str, "questions_count": int}
                    ]
                }
            ],
            "case_types": [{"name": str, "module": str}]
        }
    """
    modules = []
    case_types_seen: set[str] = set()
    case_types: list[dict] = []

    for module in app.get("modules", []):
        mod_name = _get_name(module)
        ct = module.get("case_type", "")

        forms = []
        for form in module.get("forms", []):
            forms.append({
                "name": _get_name(form),
                "xmlns": form.get("xmlns", ""),
                "question_count": len(form.get("questions", [])),
            })

        modules.append({
            "name": mod_name,
            "case_type": ct,
            "forms": forms,
        })

        if ct and ct not in case_types_seen:
            case_types_seen.add(ct)
            case_types.append({"name": ct, "module": mod_name})

    return {
        "app_id": app.get("id", ""),
        "app_name": app.get("name", ""),
        "modules": modules,
        "case_types": case_types,
    }


def extract_form_questions(app: dict, xmlns: str) -> dict | None:
    """Extract the question tree for a specific form identified by xmlns.

    Returns:
        {
            "form_name": str,
            "module_name": str,
            "case_type": str,
            "xmlns": str,
            "questions": [
                {
                    "id": str,         # e.g. "weight"
                    "type": str,       # e.g. "Int"
                    "label": str,      # e.g. "Weight (grams)"
                    "path": str,       # e.g. "/data/weight"
                    "required": bool,
                    "constraint": str | None,
                    "relevant": str | None,
                    "calculate": str | None,
                    "options": [{"value": str, "label": str}] | None,
                    "children": [...] | None,  # for groups/repeats
                }
            ]
        }
    """
    for module in app.get("modules", []):
        for form in module.get("forms", []):
            if form.get("xmlns") == xmlns:
                questions = _process_questions(form.get("questions", []))
                return {
                    "form_name": _get_name(form),
                    "module_name": _get_name(module),
                    "case_type": module.get("case_type", ""),
                    "xmlns": xmlns,
                    "questions": questions,
                }
    return None


def extract_form_json_paths(app: dict, xmlns: str) -> dict | None:
    """Extract a flat mapping of form questions to their JSON submission paths.

    This is the key tool for building PIPELINE_SCHEMAS — it tells you exactly
    what path to use for each field.

    Returns:
        {
            "form_name": str,
            "xmlns": str,
            "case_type": str,
            "paths": [
                {
                    "json_path": "form.weight",        # use this in PIPELINE_SCHEMAS
                    "question_path": "/data/weight",   # original XForm path
                    "type": "Int",                     # CommCare data type
                    "label": "Weight (grams)",         # human-readable label
                }
            ]
        }
    """
    for module in app.get("modules", []):
        for form in module.get("forms", []):
            if form.get("xmlns") == xmlns:
                paths = _build_json_paths(form.get("questions", []))
                return {
                    "form_name": _get_name(form),
                    "xmlns": xmlns,
                    "case_type": module.get("case_type", ""),
                    "paths": paths,
                }
    return None


def _process_questions(questions: list[dict]) -> list[dict]:
    """Process raw HQ question list into a clean tree."""
    result = []
    for q in questions:
        processed = {
            "id": _question_id_from_path(q.get("value", "")),
            "type": q.get("type", ""),
            "label": _get_label(q),
            "path": q.get("value", ""),
            "required": q.get("required", False),
        }

        # Optional fields — only include if present
        if q.get("constraint"):
            processed["constraint"] = q["constraint"]
        if q.get("relevant"):
            processed["relevant"] = q["relevant"]
        if q.get("calculate"):
            processed["calculate"] = q["calculate"]

        # Options for select questions
        options = q.get("options")
        if options:
            processed["options"] = [
                {"value": o.get("value", ""), "label": _get_label(o)}
                for o in options
            ]

        # Nested questions for groups/repeats
        children = q.get("children")
        if children:
            processed["children"] = _process_questions(children)

        result.append(processed)
    return result


def _build_json_paths(
    questions: list[dict], prefix: str = "form"
) -> list[dict]:
    """Build flat list of JSON paths from HQ question definitions.

    Maps each question's XForm path to its form submission JSON path.
    Rules:
        /data/weight           → form.weight
        /data/group/question   → form.group.question
        /data/repeat/question  → form.repeat[].question
    """
    paths: list[dict] = []

    for q in questions:
        q_path = q.get("value", "")
        q_type = q.get("type", "")
        label = _get_label(q)

        # Convert XForm path to JSON path
        json_path = _xform_path_to_json_path(q_path, prefix)

        # Skip group/repeat containers themselves — only include leaf questions
        if q_type in ("Group", "Repeat"):
            # Recurse into children with updated prefix
            children = q.get("children", [])
            if children:
                child_prefix = json_path
                if q_type == "Repeat":
                    child_prefix = f"{json_path}[]"
                paths.extend(_build_json_paths(children, prefix=child_prefix))
            continue

        if json_path:
            paths.append({
                "json_path": json_path,
                "question_path": q_path,
                "type": q_type,
                "label": label,
            })

    return paths


def _xform_path_to_json_path(xform_path: str, prefix: str = "form") -> str:
    """Convert an XForm question path to a form submission JSON path.

    /data/weight → form.weight
    /data/group/question → form.group.question
    """
    if not xform_path:
        return ""

    # Strip /data/ prefix
    parts = xform_path.strip("/").split("/")
    if parts and parts[0] == "data":
        parts = parts[1:]

    if not parts:
        return ""

    return f"{prefix}.{'.'.join(parts)}"


def _question_id_from_path(path: str) -> str:
    """Extract the question ID (last segment) from an XForm path."""
    if not path:
        return ""
    return path.rstrip("/").rsplit("/", 1)[-1]


def _get_name(obj: dict) -> str:
    """Extract display name from HQ object (handles dict/string name field)."""
    name = obj.get("name", "")
    if isinstance(name, dict):
        return name.get("en", next(iter(name.values()), ""))
    return str(name)


def _get_label(obj: dict) -> str:
    """Extract display label from a question object."""
    label = obj.get("label", "")
    if isinstance(label, dict):
        return label.get("en", next(iter(label.values()), ""))
    return str(label)
```

**Step 2: Verify it imports cleanly**

Run: `cd tools/commcare_mcp && python -c "import extractors; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add tools/commcare_mcp/extractors.py
git commit -m "feat: add app structure extractors and JSON path mapping"
```

---

### Task 5: Implement the MCP server

**Files:**
- Create: `tools/commcare_mcp/server.py`

**Step 1: Write the MCP server**

This wires together the HQ client and extractors into MCP tools and resources.

Create `tools/commcare_mcp/server.py`:

```python
"""CommCare HQ MCP Server.

Provides CommCare application structure context for Claude Code sessions.
Tools let you explore app modules, form questions, and JSON field paths
for building workflow pipeline schemas.

Usage (stdio, for Claude Code):
    python tools/commcare_mcp/server.py

Configuration via env vars:
    COMMCARE_HQ_DOMAIN     - Default CommCare domain
    COMMCARE_HQ_API_KEY    - API key as "user@email.com:apikey"
    COMMCARE_HQ_URL        - HQ base URL (default: https://www.commcarehq.org)
"""

from __future__ import annotations

import logging
from pathlib import Path

from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

RESOURCES_DIR = Path(__file__).parent / "resources"

mcp = FastMCP(
    "commcare-hq",
    instructions=(
        "CommCare HQ application structure server. Use these tools to understand "
        "CommCare app form structure, question types, and JSON field paths. "
        "This is especially useful when building or debugging workflow pipeline "
        "schemas (PIPELINE_SCHEMAS) that map form fields to data extraction paths."
    ),
)


# --- Resources ---


@mcp.resource("commcare://app-schema")
def app_schema_resource() -> str:
    """CommCare app structure reference — question types, case properties, validation rules."""
    return (RESOURCES_DIR / "app_schema.md").read_text(encoding="utf-8")


@mcp.resource("commcare://xml-reference")
def xml_reference_resource() -> str:
    """CommCare XForm/Suite/Case XML structure reference."""
    return (RESOURCES_DIR / "xml_reference.md").read_text(encoding="utf-8")


@mcp.resource("commcare://data-patterns")
def data_patterns_resource() -> str:
    """How CommCare form submission JSON is structured — path mapping rules and pitfalls."""
    return (RESOURCES_DIR / "data_patterns.md").read_text(encoding="utf-8")


# --- Tools ---


@mcp.tool()
async def list_apps(domain: str = "") -> dict:
    """List all CommCare applications for a domain.

    Returns app names, IDs, module counts, and form counts.
    Use this to find the app_id needed for other tools.

    Args:
        domain: CommCare domain name (optional, uses COMMCARE_HQ_DOMAIN env var if not set)
    """
    from tools.commcare_mcp.hq_client import list_apps as _list_apps

    try:
        apps = await _list_apps(domain or None)
        return {"apps": apps, "count": len(apps)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_app_structure(app_id: str, domain: str = "") -> dict:
    """Get the module/form/case-type structure of a CommCare application.

    Shows the full app tree: modules → forms (with xmlns) → case types.
    Use this to understand how an app is organized before drilling into specific forms.

    Args:
        app_id: The CommCare application ID (from list_apps)
        domain: CommCare domain name (optional, uses env var default)
    """
    from tools.commcare_mcp.extractors import extract_app_structure
    from tools.commcare_mcp.hq_client import get_app

    try:
        app = await get_app(domain or None, app_id)
        return extract_app_structure(app)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_form_questions(app_id: str, xmlns: str, domain: str = "") -> dict:
    """Get the full question tree for a specific form.

    Shows all questions with their types, labels, constraints, skip logic,
    and nesting (groups/repeats). Use this to understand what data a form collects.

    Args:
        app_id: The CommCare application ID
        xmlns: The form's xmlns identifier (from get_app_structure)
        domain: CommCare domain name (optional)
    """
    from tools.commcare_mcp.extractors import extract_form_questions
    from tools.commcare_mcp.hq_client import get_app

    try:
        app = await get_app(domain or None, app_id)
        result = extract_form_questions(app, xmlns)
        if result is None:
            return {"error": f"Form with xmlns '{xmlns}' not found in app {app_id}"}
        return result
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_form_json_paths(app_id: str, xmlns: str, domain: str = "") -> dict:
    """Map form questions to their JSON submission paths for pipeline schemas.

    THIS IS THE KEY TOOL for building PIPELINE_SCHEMAS. It shows exactly what
    path each form question will have in submitted form JSON.

    Example output:
        {"json_path": "form.weight", "type": "Int", "label": "Weight (grams)"}
        {"json_path": "form.child_info.birth_weight", "type": "Decimal", "label": "Birth Weight"}

    Use the json_path values directly in PIPELINE_SCHEMAS field definitions:
        {"name": "weight", "path": "form.weight", "transform": "float"}

    Args:
        app_id: The CommCare application ID
        xmlns: The form's xmlns identifier (from get_app_structure)
        domain: CommCare domain name (optional)
    """
    from tools.commcare_mcp.extractors import extract_form_json_paths
    from tools.commcare_mcp.hq_client import get_app

    try:
        app = await get_app(domain or None, app_id)
        result = extract_form_json_paths(app, xmlns)
        if result is None:
            return {"error": f"Form with xmlns '{xmlns}' not found in app {app_id}"}
        return result
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    mcp.run(transport="stdio")
```

**Step 2: Verify it starts without errors (will fail on missing env vars, that's OK)**

Run: `python tools/commcare_mcp/server.py --help 2>&1 || echo "Server module loads"`
Expected: Server loads (may error on missing stdin transport when not run by MCP client, that's fine)

**Step 3: Commit**

```bash
git add tools/commcare_mcp/server.py
git commit -m "feat: implement CommCare HQ MCP server with 4 tools and 3 resources"
```

---

### Task 6: Configure Claude Code MCP integration

**Files:**
- Modify: `.claude/mcp.json` (create if doesn't exist)

**Step 1: Check if .claude/mcp.json exists**

Run: `cat .claude/mcp.json 2>/dev/null || echo "Does not exist"`

**Step 2: Add or update the MCP server configuration**

Add the `commcare-hq` server entry. The env vars should use placeholder values — the user will fill in their real API key.

`.claude/mcp.json`:
```json
{
  "mcpServers": {
    "commcare-hq": {
      "command": "python",
      "args": ["tools/commcare_mcp/server.py"],
      "env": {
        "COMMCARE_HQ_DOMAIN": "",
        "COMMCARE_HQ_API_KEY": "",
        "COMMCARE_HQ_URL": "https://www.commcarehq.org"
      }
    }
  }
}
```

Note: If `.claude/mcp.json` already exists with other servers (like Sentry), merge this entry into the existing `mcpServers` object.

**Step 3: Add `.claude/mcp.json` to `.gitignore` if not already there**

This file contains API keys and should NOT be committed. Check if it's already gitignored:

Run: `grep -q "mcp.json" .gitignore && echo "Already ignored" || echo ".claude/mcp.json" >> .gitignore`

**Step 4: Commit the gitignore update (if changed)**

```bash
git add .gitignore
git commit -m "chore: gitignore .claude/mcp.json (contains API keys)"
```

---

### Task 7: Test the server end-to-end with real data

**Files:** None (manual testing)

**Step 1: Configure the MCP server with real credentials**

Edit `.claude/mcp.json` and fill in:
- `COMMCARE_HQ_DOMAIN`: The domain for the KMC opportunity
- `COMMCARE_HQ_API_KEY`: User's API key in `email:key` format

**Step 2: Test the server directly via Python**

Run a quick smoke test that exercises the HQ client and extractors:

```bash
COMMCARE_HQ_DOMAIN=your-domain COMMCARE_HQ_API_KEY="user@email.com:key" python -c "
import asyncio
import sys
sys.path.insert(0, 'tools/commcare_mcp')
from hq_client import list_apps
apps = asyncio.run(list_apps())
print(f'Found {len(apps)} apps')
for app in apps[:5]:
    print(f'  {app[\"name\"]} (id={app[\"id\"]}, {app[\"form_count\"]} forms)')
"
```

Expected: List of apps from the domain.

**Step 3: Test form JSON path extraction**

Once you have an app_id from Step 2, test the path extraction:

```bash
COMMCARE_HQ_DOMAIN=your-domain COMMCARE_HQ_API_KEY="user@email.com:key" python -c "
import asyncio, json, sys
sys.path.insert(0, 'tools/commcare_mcp')
from hq_client import get_app
from extractors import extract_app_structure, extract_form_json_paths

app = asyncio.run(get_app(None, 'YOUR_APP_ID'))
structure = extract_app_structure(app)
print(json.dumps(structure, indent=2)[:2000])

# Get paths for the first form
first_xmlns = structure['modules'][0]['forms'][0]['xmlns']
paths = extract_form_json_paths(app, first_xmlns)
print(json.dumps(paths, indent=2)[:2000])
"
```

Expected: App structure tree and form JSON path mappings.

**Step 4: Restart Claude Code to pick up the new MCP server**

After configuring `.claude/mcp.json`, restart the Claude Code session. The server should appear in the available tools. Test by asking Claude to `list_apps` or `get_form_json_paths`.

---

### Task 8: Fix the server module import paths

**Files:**
- Possibly modify: `tools/commcare_mcp/server.py`

The import paths in `server.py` use `from tools.commcare_mcp.hq_client import ...` which assumes the working directory is the repo root. This may need adjustment depending on how Claude Code launches MCP servers.

**Step 1: Test the import path**

Run from repo root:
```bash
python -c "import sys; sys.path.insert(0, '.'); from tools.commcare_mcp.hq_client import list_apps; print('OK')"
```

If this fails, the server.py imports need to use relative imports instead:
```python
# Change from:
from tools.commcare_mcp.hq_client import list_apps as _list_apps
# To:
from hq_client import list_apps as _list_apps
```

**Step 2: Verify the server runs from repo root**

```bash
cd /path/to/commcare-connect
python tools/commcare_mcp/server.py
```

**Step 3: Fix imports if needed and commit**

```bash
git add tools/commcare_mcp/server.py
git commit -m "fix: adjust import paths for MCP server"
```
