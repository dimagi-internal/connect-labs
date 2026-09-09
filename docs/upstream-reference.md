# Upstream Connect Reference

> **Status (2026-09-08 doc-regen):** corrected and current. The three errors the previous regen flagged are fixed — the `commcarehq` row is gone (that app is *retained* locally, not removed), the `flags` row now warns about the name collision with today's unrelated `connect_labs/flags/` app, and the local package path + production host are right (`connect_labs/`, `https://connect.dimagi.com`). Upstream URLs still say `commcare_connect/` because that is the package name **in the upstream repo**. This doc is a pointer to upstream code, not a description of this repo — for what labs looks like now, see [CLAUDE.md](../CLAUDE.md).

This labs repo was forked from [dimagi/commcare-connect](https://github.com/dimagi/commcare-connect). Several production apps were removed during the March 2026 simplification. Here's where to find the original code if you need it for reference.

## Removed Apps

| App | Purpose | Upstream Location |
|-----|---------|-------------------|
| `data_export` | CSV/JSON export API for opportunity data. Provides `/export/labs_record/` endpoint that labs consumes via HTTP. | [commcare_connect/data_export/](https://github.com/dimagi/commcare-connect/tree/main/commcare_connect/data_export) |
| `form_receiver` | Receives form submissions from CommCare HQ, creates UserVisit/CompletedWork records. | [commcare_connect/form_receiver/](https://github.com/dimagi/commcare-connect/tree/main/commcare_connect/form_receiver) |
| `reports` | Invoice and delivery reports with django-tables2 and django-filters. | [commcare_connect/reports/](https://github.com/dimagi/commcare-connect/tree/main/commcare_connect/reports) |
| `microplanning` | Geographic work area mapping with PostGIS and vector tiles. | [commcare_connect/microplanning/](https://github.com/dimagi/commcare-connect/tree/main/commcare_connect/microplanning) |
| `flags` | Waffle feature flags for production rollouts. **Name collision:** `connect_labs/flags/` today is an unrelated labs app (flag-type `LocalLabsRecord`s on workflow runs), not this. | [commcare_connect/flags/](https://github.com/dimagi/commcare-connect/tree/main/commcare_connect/flags) |
| `connect_id_client` | Client library for ConnectID push notifications. | [commcare_connect/connect_id_client/](https://github.com/dimagi/commcare-connect/tree/main/commcare_connect/connect_id_client) |
| `commcarehq_provider` | django-allauth OAuth2 provider for CommCare HQ login. | [commcare_connect/commcarehq_provider/](https://github.com/dimagi/commcare-connect/tree/main/commcare_connect/commcarehq_provider) |
| `deid` | De-identification utilities. | [commcare_connect/deid/](https://github.com/dimagi/commcare-connect/tree/main/commcare_connect/deid) |

## Gutted Apps

| App | What Was Removed | What Remains |
|-----|-----------------|--------------|
| `opportunity` | All views, URLs, admin, forms, tables, management commands, tests (~22K LOC) | `models.py` (all model definitions), `apps.py`, `__init__.py`, `migrations/` |

## How Labs Accesses Production Data

Labs does **not** run data_export locally. Instead:

1. `LabsRecordAPIClient` (in `connect_labs/labs/integrations/connect/api_client.py`) makes HTTP calls to the **production** Connect server at `https://connect.dimagi.com/export/labs_record/`.
2. The production server runs the upstream `data_export` app which queries the `LabsRecord` model.
3. Labs receives JSON responses and wraps them in `LocalLabsRecord` proxy objects.

## Fork Point

This repo diverged from upstream around October 2025 and was later split out of `dimagi/commcare-connect` into its own repo at `dimagi-internal/connect-labs`. The canonical branch is now `main`. (Historical note: while it lived as a fork, labs-specific development happened on a `labs-main` branch — references to `labs-main` in older plans/docs refer to that era and now mean `main`.)
