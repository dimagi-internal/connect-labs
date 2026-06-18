# DEPRECATED — `mbw_monitoring` (MBW Monitoring v1)

**Do not use this package as a pattern, and do not extend it.** It is legacy
v1 code retained for one reason only: a few **pre-existing production workflow
instances still render from it.**

## What this is

The original v1 MBW monitoring dashboard: a Python job handler
(`handle_mbw_monitoring_job`) that computes GPS / follow-up / quality metrics,
SSE streaming endpoints under `/custom_analysis/mbw_monitoring/`, and a large
in-template React bundle. This architecture predates the SQL-native,
pipeline-pure approach and is **not** how MBW (or any) dashboards should be
built now.

## Why it's still here

Deleting the package would orphan live instances that hold **real review
data** — notably defs **897** and **2642** on opportunity **765** (Mother Baby
Wellness, Nigeria), which contain ~100-FLW review sessions (renewal / probation
/ suspended decisions, some with reviewer notes, from March/April 2026). Those
runs render from the package's endpoints + job handler, so the code stays until
that data is migrated or intentionally retired.

## What you should use instead

**`mbw_auditing_v5`** (`commcare_connect/workflow/templates/mbw_auditing_v5.py`

- `_render.js`) is the current production MBW template: SQL-native, pipeline
  pure, no Python job handler, saved-runs aware. Copy patterns from there.

## Guardrails in place

- `TEMPLATE["deprecated"] = True` — excluded from `list_templates()`, so it
  does not appear in the create-from-template menu, the `list_templates` MCP
  tool, or the `workflow-templates` skill's examples.
- `create_workflow_from_template` refuses deprecated keys — **no new instances
  can be created** from it.
- It remains in the registry via `get_template()` only so the existing
  instances keep rendering.

If/when 897 + 2642's data is exported or migrated, this whole package (plus
`job_handlers/mbw_monitoring.py` and the `/custom_analysis/mbw_monitoring/`
route in `config/urls.py`) can be deleted outright.
