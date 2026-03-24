# Codebase Improvement Burndown

## Critical — Runtime Crashes

- [x] **Fix `update_record` wrong positional args** — `tasks/data_access.py:279,294,310,334,359`. *(Fixed: switched to keyword args. 10 unit tests added.)*
- [x] **Fix broken imports in `custom_analysis/mbw/views.py:20-28`**. *(Fixed: corrected import paths to `workflow.templates.mbw_monitoring`.)*
- [x] **Fix broken import in `audit/management/commands/test_search_readers.py:7`**. *(Fixed: deleted dead code.)*
- [x] **Fix broken management command `cleanup_duplicate_assessments.py`**. *(Fixed: deleted dead code.)*
- [x] **Fix missing webpack bundle reference**. *(Fixed: updated to `pipeline-editor-bundle.js`.)*

## High — Security

- [x] **Fix SSRF in `solicitations/views.py:87-121`**. *(Fixed: added `validate_url_safe()` with DNS resolution check. 14 unit tests.)*
- [x] **Fix open redirect in Connect OAuth callback**. *(Fixed: added validation. 2 unit tests.)*
- [x] **Set `SESSION_COOKIE_SECURE = True`**. *(Fixed: set both to `True`.)*
- [x] **Add `@login_required` to `clear_context`**. *(Fixed: added decorator. 2 unit tests.)*
- [x] **Re-enable `_is_dimagi_user` email check**. *(Fixed: added `openid` scope, fetch email from OIDC `/o/userinfo/`, created shared `is_dimagi_user()` utility, replaced all 5 copies.)*

## High — Data Layer

- [ ] **Optimize `get_record_by_id`** — `api_client.py:160-183` fetches ALL records then scans linearly. Add server-side `id` filtering.
- [ ] **Add pagination to `get_records`** — `api_client.py:144-154` makes a single GET with no next-page handling.
- [ ] **Implement OAuth token refresh** — `refresh_token` stored at login but never used. Users must re-login on expiry.
- [ ] **Fix race condition in `get_or_create_run`** — `workflow/data_access.py:648-682` non-atomic check-then-create.

## Medium — Error Handling

- [x] **Add logging to silent exception catches**. *(Fixed: added `logger.debug` and `logger.warning`.)*
- [x] **Wrap bare `raise_for_status()` calls**. *(Fixed: added try/except with logging across coverage, audit, commcare API client, funder_dashboard.)*
- [x] **Sanitize error responses**. *(Fixed: replaced `str(e)` with generic messages in 65 views across tasks, workflow, audit, ai, funder_dashboard.)*
- [x] **Reduce API payload log level**. *(Fixed: INFO → DEBUG.)*

## Medium — Frontend

- [x] **Fix Alpine.js double-load**. *(Fixed: removed CDN script tags.)*
- [x] **Fix duplicate `class=` attribute**. *(Fixed: merged into single class attr.)*
- [x] **Remove CDN Tailwind from production templates**. *(Fixed: replaced with project's compiled CSS.)*
- [x] **Remove debug `console.log` statements**. *(Fixed: removed from dashboard.js.)*
- [x] **Add missing `alt` attributes**. *(Fixed: added to timeline.html dynamic images.)*
- [x] **Replace hardcoded URLs with `{% url %}` tags**. *(Fixed: login, explorer, audit_of_audits templates.)*

## Medium — Code Quality

- [x] **Fix double decorator**. *(Fixed: removed duplicates.)*
- [x] **Fix `CELERY_TASK_ALWAYS_EAGER` string bug**. *(Fixed: `"False"` → `"0"`.)*
- [ ] **Extract shared `BaseDataAccess`** — `audit`, `tasks`, `solicitations` re-implement the same init.
- [ ] **Remove deprecated analysis pipeline callers** — `stream_analysis_pipeline`/`run_analysis_pipeline` still called in `labs/analysis/views.py:79`, `chc_nutrition/views.py:94,279`.
- [x] **Clean up deprecated `AuditTemplateRecord`**. *(Fixed: removed.)*
- [x] **Remove deprecated aliases in `workflow/data_access.py`**. *(Fixed: removed.)*
- [x] **Replace tempfile CSV pattern with `io.BytesIO`**. *(Fixed: tasks and workflow data_access.)*

## Low — Cleanup

- [ ] **Fix unofficial Font Awesome Pro CDN** — `account/base.html:12` loads from unlicensed GitHub CDN.
- [ ] **Add SRI hashes to CDN scripts** — Leaflet, Chart.js, Marked.js, Prism all loaded without integrity checks.
- [x] **Remove unused static files**. *(Fixed: deleted `commcare-logo-color.svg`, `datetime-utils.js`.)*
- [x] **Remove unused npm devDependencies**. *(Fixed: removed `gulp-concat`, `pixrem`, `node-sass-tilde-importer`.)*
- [x] **Increase HSTS to meaningful duration**. *(Fixed: 60s → 3600s.)*
- [x] **Fix file handle leak in e2e conftest**. *(Fixed: added try/finally cleanup.)*
- [ ] **Extract large inline `<script>` blocks** — `audit_creation_wizard.html` (~650 lines), `bulk_assessment.html` (~500 lines), `timeline.html` (~600 lines).
