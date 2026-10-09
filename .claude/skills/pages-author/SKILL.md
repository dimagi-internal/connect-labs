---
name: pages-author
description: Use when building a labs page -- an organisation's or programme's landing page, a home page, a screen of its own at /labs/p/... Triggers on "build a program hub", "make a landing page for org/program/opp X", "set the org's home page".
---

# Authoring pages

A page is a **workflow with no runs** (`kind: "page"`, `connect_labs/workflow/page_mode.py`), written and edited with the workflow tools. The old card "surfaces" (`pages_*` tools, `type="surface"` records) were retired on 2026-10-09.

Follow the **workflow-author** skill, section "Build a page":

1. `workflow_create_from_template(template_key="page_blank", program_id=…)` (or `opportunity_id`; a real Connect organisation can own one with `organization_id=<int>`).
2. `workflow_update_definition` to set `page: {slug}` and declare `workflow_sources` / `supply_sources` / `config_reads`.
3. Edit the JSX with `workflow_update_render_code`. It receives `scope`, `config`, `workflows`, `supply` and the usual workflow props.
4. Open it at `/labs/p/<org|programme|opportunity>/<key>/<slug>/`, or make it a scope's home with `labs_config_set(namespace="labs", …, patch={"home": {"fill": {"workflow": <id>, "program_id": <owner>}}})`.

Contract: `connect_labs/workflow/WORKFLOW_REFERENCE.md` §16. Design: `docs/superpowers/specs/2026-10-09-labs-scope-config-design.md`.
