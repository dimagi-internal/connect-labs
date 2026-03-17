# Codebase Simplification Design

**Date:** 2026-03-11
**Branch:** jj/refactor
**Goal:** Remove all production CommCare Connect code not used by labs to simplify the codebase for labs-only development.

## Context

This repo was forked from CommCare Connect with the original intent of merging back. That's no longer a goal. Multiple people now vibe-code on labs, and the inherited production code (~36K LOC across 15 apps) creates confusion, slows navigation, and bloats the project.

## Approach: Surgical App Removal

Incremental removal of dead apps, one commit per logical step, tests verified between steps.

## What Gets Removed

### Apps Deleted Entirely (zero labs dependencies)

| App | Size | Reason |
|-----|------|--------|
| `form_receiver/` | 1.7K LOC | CommCare form submission handler — labs uses API |
| `reports/` | 1.8K LOC | Payment/delivery reports — labs has workflow templates |
| `microplanning/` | 1K LOC | Geographic work areas — unused by labs |
| `flags/` | 650 LOC | Waffle feature flags — unused by labs |
| `connect_id_client/` | 320 LOC | ConnectID push notifications — unused by labs |
| `commcarehq/` | 260 LOC | HQServer model + API — labs uses separate HQ client |
| `commcarehq_provider/` | 66 LOC | AllAuth OAuth provider — unused by labs |
| `data_export/` | 876 LOC | Export API — labs calls production endpoint via HTTP |
| `deid/` | ~200 LOC | De-identification — unused by labs |

### Opportunity App — Gutted to LabsRecord Only

- **Delete:** All views, forms, tables, URLs, admin, management commands, helpers, signals, tests
- **Keep:** LabsRecord model in models.py + supporting model dependencies it FKs to
- **Keep:** All migrations (needed for DB state consistency)
- **Refactor:** `labs/configurable_ui/linking.py` to remove UserVisit import

### Directories Deleted

| Directory | Reason |
|-----------|--------|
| `deploy/` | Ansible/Kamal production deployment — labs uses ECS Fargate |
| `locale/` | Empty i18n directory |

### Files Deleted

| File | Reason |
|------|--------|
| `config/settings/production.py` | Production settings — labs uses labs_aws.py |
| `config/settings/staging.py` | Staging settings — unused by labs |
| `.github/workflows/deploy.yml` | Production deploy workflow (if present) |

## What Gets Updated

- `config/settings/base.py` — Remove deleted apps from INSTALLED_APPS, remove allauth
- `config/urls.py` — Remove URL includes for deleted apps
- `config/api_router.py` — Remove registrations for deleted apps
- `requirements/base.in` — Remove deps only used by deleted apps (waffle, django-allauth, twilio, etc.)
- `requirements/production.in` — Remove or reduce to gunicorn+psycopg2
- `Dockerfile` — Remove production.txt install step
- `CLAUDE.md` — Update to reflect simplified structure
- CI config — Update if needed

## What Gets Created

- `docs/upstream-reference.md` — Guide to finding original CommCare Connect code on GitHub (data_export, opportunity views, form_receiver, etc.) with exact URLs and commit SHAs

## What Stays Untouched

- **All 7 labs apps:** labs, audit, tasks, workflow, ai, solicitations, coverage
- `solicitations/`
- `users/`, `organization/`, `program/` — small, kept as-is
- `web/` — base templates
- `multidb/` — DB routing
- All frontend code (components/, webpack/, tailwind/, lib/)
- `.claude/`, `tools/commcare_mcp/`
- `docker-compose.yml`, `docker/`

## Execution Order

Each step is a separate commit with tests run between:

1. **Remove zero-dependency apps** — form_receiver, reports, microplanning, flags, connect_id_client, commcarehq, commcarehq_provider, deid
2. **Remove data_export** — delete app, write upstream-reference.md
3. **Gut opportunity app** — keep only LabsRecord model + migrations
4. **Refactor linking.py** — remove UserVisit import, use API data
5. **Clean up config** — settings, URLs, api_router, requirements
6. **Remove deploy infrastructure** — deploy/, locale/, production/staging settings
7. **Update Dockerfile and CI**
8. **Update documentation** — CLAUDE.md, AGENTS.md, MEMORY.md

## Estimated Impact

- **Code removed:** ~36K LOC Python, ~400KB
- **Apps removed:** 9 entirely + 1 gutted
- **Directories removed:** 2
- **Settings files removed:** 2
- **Dependencies removed:** ~10 packages
