# Workflow Docs & Tooling DRY Refactor — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create a single source of truth (`WORKFLOW_REFERENCE.md`) for workflow authoring, then wire up CLAUDE.md, the skill, and the in-product AI agent to reference it instead of duplicating material.

**Architecture:** One canonical reference file in the workflow directory. The skill becomes a process guide that links to it. The in-product agent loads it at module init. CLAUDE.md gets a brief section pointing to it.

**Tech Stack:** Markdown (reference), Python (agent refactor), SKILL.md (skill rewrite)

---

### Task 1: Create `WORKFLOW_REFERENCE.md`

**Files:**
- Create: `connect_labs/workflow/WORKFLOW_REFERENCE.md`

**Step 1: Write the reference document**

Create the canonical reference with these sections. Content is synthesized from existing sources (types.ts, data_access.py, skill, agent instructions, template examples):

```markdown
# Workflow Engine Reference

Complete reference for building workflow templates. This file is the single source
of truth — the Claude Code skill, in-product AI agent, and CLAUDE.md all reference it.

## Template Anatomy

Each template is a single Python file in `connect_labs/workflow/templates/`.
Auto-discovered by the registry in `__init__.py`.

### Required Exports

DEFINITION dict — workflow configuration (name, statuses, config)
RENDER_CODE string — React JSX component
TEMPLATE dict — ties everything together with key, icon, color
Optional: PIPELINE_SCHEMA (single) or PIPELINE_SCHEMAS (multiple)

### Minimal Example (performance_review.py)
[Include trimmed version of performance_review.py showing structure]

### Multi-Pipeline Example (kmc_longitudinal.py)
[Show the PIPELINE_SCHEMAS pattern with aliases]

## Pipeline Schema Deep-Dive

### Schema Structure
[Full schema with all fields documented]

### Fields
- name: field identifier (used in rows.computed.{name} or rows.custom_fields.{name})
- path: dot-notated JSON path (e.g., "form.anthropometric.child_weight_visit")
- paths: array of fallback paths — tried in order, first non-null wins
- aggregation: first, last, count, sum, avg, min, max, list, count_unique
- transform: "float", "int", "kg_to_g", "date", "string" (or omit for raw string)
- filter_path / filter_value: only include rows where filter_path == filter_value
- description: human-readable label

### Terminal Stage
- "visit_level": one row per form submission. Fields in row.computed.{name}
- "aggregated": one row per grouping_key. Custom fields in row.custom_fields.{name}

### Grouping Key
- "username": group by FLW (most common)
- "entity_id": group by delivery unit
- "deliver_unit_id": group by delivery unit ID

### Linking Field
For visit_level pipelines, linking_field specifies which field connects
visits to a logical entity (e.g., beneficiary_case_id links visits to a child).

### Data Source
[Document data_source options]

### Histograms
[Document histogram computation structure]

## Discovering Field Paths

### Using the MCP Server (Claude Code)
1. get_opportunity_apps(opportunity_id) → get domain and app IDs
2. get_app_structure(domain, app_id) → see modules, forms, xmlns
3. get_form_json_paths(xmlns, domain, app_id) → exact JSON paths for each question
4. Use json_path values directly in PIPELINE_SCHEMAS field definitions

### Without MCP (manual / in-product agent)
- CommCare HQ → Application → Form → question ID maps to form.{group}.{question_id}
- Nested groups: form.{group1}.{group2}.{question_id}
- Case properties: form.case.@case_id, form.case.update.{property}
- Meta fields: form.meta.timeEnd, form.meta.instanceID, form.meta.location.#text

### Common Meta Paths
[Table of always-available paths]

## Render Code Contract

### Function Signature
function WorkflowUI({ definition, instance, workers, pipelines, links, actions, onUpdateState })

### Constraints
- Must define a function named WorkflowUI (not const/let — use function declaration)
- Use var for all variable declarations (Babel standalone + eval limitation)
- No imports — only React global is available
- CDN libs available: Chart.js 4.4.0 (window.Chart), Leaflet 1.9.4 (window.L)
- Tailwind CSS classes available for styling

### Props Reference
[Full props table derived from types.ts]

### Pipeline Data Access
[visit_level vs aggregated patterns with code examples]

## Actions API

### Task Management
[createTask, openTaskCreator, getTaskDetail, updateTask signatures]

### Audit Creation
[createAudit, getAuditStatus, streamAuditProgress, cancelAudit]

### Job Management
[startJob, streamJobProgress, cancelJob, deleteRun]

### OCS Integration
[checkOCSStatus, listOCSBots, createTaskWithOCS, initiateOCSSession]

### MBW-Specific
[saveWorkerResult, completeRun]

### AI Transcript
[getAITranscript, getAISessions, saveAITranscript]

## Common UI Patterns

### KPI Summary Cards
[Code snippet]

### Sortable/Filterable Table
[Code snippet]

### Status Badges
[Code snippet with color map]

### Chart.js Integration
[Code snippet showing window.Chart usage]

### Leaflet Map
[Code snippet showing window.L usage]

### SSE Pipeline Loading
[Code snippet for EventSource pattern]

### Progress Tracking (Jobs/Audits)
[Code snippet]

## Building from External Specs

### Process
1. Analyze the source document — identify indicators, data points, groupings
2. Map each indicator to a CommCare form question (use MCP or manual inspection)
3. Decide terminal_stage: do you need per-visit rows or per-worker aggregates?
4. Write PIPELINE_SCHEMAS with correct paths, aggregations, transforms
5. Design RENDER_CODE to visualize the indicators
6. Wire into TEMPLATE export

### Indicator → Pipeline Field Mapping
[Examples of common indicator types and how to express them]

### Validation Checklist
- Template key is unique (check __init__.py)
- All field paths verified via MCP or manual inspection
- Test with ?edit=true to verify pipeline data is non-empty
- Check browser console for Babel transpilation errors
```

**Step 2: Verify the document renders correctly**

Run: `python -c "open('connect_labs/workflow/WORKFLOW_REFERENCE.md').read()"`
Expected: No errors, file exists and is readable.

**Step 3: Commit**

```bash
git add connect_labs/workflow/WORKFLOW_REFERENCE.md
git commit -m "docs: create WORKFLOW_REFERENCE.md as single source of truth for workflow authoring"
```

---

### Task 2: Update CLAUDE.md with Workflow Engine section

**Files:**
- Modify: `CLAUDE.md` (insert after App Map section, before Key Commands)

**Step 1: Add the Workflow Engine section**

Insert between the App Map table and Key Commands:

```markdown
## Workflow Engine

Templates are single Python files in `workflow/templates/` exporting DEFINITION (statuses, config), RENDER_CODE (React JSX string transpiled by Babel), and optionally PIPELINE_SCHEMAS (CommCare form field extraction). The registry auto-discovers them. Pipeline schemas map CommCare form JSON paths to extracted fields with aggregations and transforms. Render code receives `{definition, instance, workers, pipelines, links, actions, onUpdateState}` as props.

Use the MCP server's `get_form_json_paths` tool to discover correct field paths when building pipeline schemas.

**Full reference:** [WORKFLOW_REFERENCE.md](connect_labs/workflow/WORKFLOW_REFERENCE.md)
```

**Step 2: Verify CLAUDE.md is valid**

Visually check the file reads correctly.

**Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add Workflow Engine section to CLAUDE.md linking to reference"
```

---

### Task 3: Rewrite the `workflow-templates` skill

**Files:**
- Modify: `.claude/skills/workflow-templates/SKILL.md`

**Step 1: Rewrite the skill as a process guide**

The skill should focus on the *process* of building a template, NOT duplicate reference material. It should link to WORKFLOW_REFERENCE.md for details.

New structure:
1. Phase 1 (conditional): Interpreting external specs — analyze document, use MCP tools
2. Phase 2: Build the template — with links to reference for schema/render/action details
3. Validation checklist

Key change: Remove all the inline props tables, action examples, and pattern code. Replace with "See WORKFLOW_REFERENCE.md sections X and Y."

**Step 2: Verify skill file**

Run: `python -c "open('.claude/skills/workflow-templates/SKILL.md').read()"`

**Step 3: Commit**

```bash
git add .claude/skills/workflow-templates/SKILL.md
git commit -m "refactor: rewrite workflow-templates skill as process guide referencing WORKFLOW_REFERENCE.md"
```

---

### Task 4: Refactor `workflow_agent.py` to load from reference

**Files:**
- Modify: `connect_labs/ai/agents/workflow_agent.py`

**Step 1: Add reference file loading**

At module level, load WORKFLOW_REFERENCE.md and include it in agent instructions:

```python
from pathlib import Path

_REFERENCE_PATH = Path(__file__).resolve().parents[2] / "workflow" / "WORKFLOW_REFERENCE.md"

def _load_reference() -> str:
    """Load the workflow reference document for agent context."""
    try:
        return _REFERENCE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning(f"Workflow reference not found at {_REFERENCE_PATH}")
        return ""
```

**Step 2: Slim down WORKFLOW_AGENT_INSTRUCTIONS**

Replace the hardcoded schema/props/actions documentation with a reference to the loaded file. Keep only the agent-specific instructions (context awareness, tool usage rules, critical rules):

```python
WORKFLOW_AGENT_INSTRUCTIONS = f"""
You are an expert helping users build data-driven workflows with custom React UIs.

## Context Awareness
[Keep existing context awareness section]

## Reference
The full workflow authoring reference is below. Use it to understand template structure,
pipeline schemas, render code constraints, and available actions.

{_load_reference()}

## Tools Available
[Keep existing tools section]

## When to Use Which Tool
[Keep existing decision tree]

## CRITICAL RULES
[Keep existing rules]
"""
```

**Step 3: Run existing tests to ensure nothing breaks**

Run: `pytest connect_labs/ai/ -v --tb=short`
Expected: All tests pass (or no tests exist for this module — verify).

**Step 4: Commit**

```bash
git add connect_labs/ai/agents/workflow_agent.py
git commit -m "refactor: load WORKFLOW_REFERENCE.md in workflow agent instead of hardcoding docs"
```

---

### Task 5: Update workflow README to link to reference

**Files:**
- Modify: `connect_labs/workflow/README.md`

**Step 1: Add reference link**

Add a prominent link near the top of the README:

```markdown
**Full authoring guide:** [WORKFLOW_REFERENCE.md](WORKFLOW_REFERENCE.md) — template anatomy, pipeline schemas, render code contract, actions API
```

**Step 2: Commit**

```bash
git add connect_labs/workflow/README.md
git commit -m "docs: link workflow README to WORKFLOW_REFERENCE.md"
```

---

### Task 6: Update auto-memory

**Files:**
- Modify: `~/.claude/projects/.../memory/MEMORY.md`

**Step 1: Add entry about the DRY refactor**

Add a note about the single source of truth pattern so future sessions know about it.

**Step 2: Done — no commit needed for memory files**
