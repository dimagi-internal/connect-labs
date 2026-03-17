# Audit App

Quality assurance review of frontline worker (FLW) visits with AI-powered validation.

Audits are created by selecting visit criteria (date range, count per FLW, etc.), extracting images and readings from CommCare form submissions, and presenting them for human review in a bulk assessment UI. Optional AI agents can pre-review each image.

## Key Files

| File                 | Purpose                                                                             |
| -------------------- | ----------------------------------------------------------------------------------- |
| `models.py`          | `AuditSessionRecord` proxy model with assessment result management                  |
| `data_access.py`     | `AuditDataAccess` — wraps LabsRecordAPIClient + AnalysisPipeline for visit fetching |
| `views.py`           | 23 views: creation wizard, bulk assessment UI, async job management, AI review      |
| `ai_review.py`       | `run_single_ai_review()` — runs AI validation agents on images                      |
| `tasks.py`           | `run_audit_creation` Celery task — multi-stage async audit creation                 |
| `analysis_config.py` | `AUDIT_EXTRACTION_CONFIG` — field extraction config for AnalysisPipeline            |
| `tables.py`          | django-tables2 table definitions for audit list                                     |
| `urls.py`            | URL routing under `/audit/`                                                         |

## Data Model

- **Experiment:** `"audit"`
- **Type:** `"AuditSession"`
- **Proxy model:** `AuditSessionRecord`

Key properties on `AuditSessionRecord`:

- `title`, `tag`, `status` (`in_progress` / `completed`), `overall_result` (`pass` / `fail` / None)
- `visit_ids`, `visit_results` — per-visit assessment tracking
- `criteria` — `AuditCriteria` dict (audit_type, date range, counts, sample %)
- `workflow_run_id` — reference when created from a workflow

Assessment methods: `set_visit_result()`, `set_assessment()`, `get_progress_stats()`, `get_assessment_stats()`

## Data Flow

1. User configures criteria in creation wizard or workflow triggers `run_audit_creation` Celery task
2. `AuditDataAccess.get_visit_ids_for_audit()` fetches and filters visits via AnalysisPipeline
3. `extract_images_for_visits()` downloads image blob IDs and related fields from form_json
4. `create_audit_session()` persists session with visit data via LabsRecordAPIClient
5. Optional: AI agent reviews each image (`run_single_ai_review()`)
6. Human reviews in bulk assessment UI, saves incrementally via `save_audit_session()`
7. Session completed with overall result via `complete_audit_session()`

## Cross-App Connections

- **Depends on:** `labs.analysis.pipeline` (visit data), `labs.ai_review_agents` (AI agents), `labs.integrations.connect.api_client`
- **Used by:** `workflow/` (creates audits via `AuditDataAccess`), `tasks/` (references `audit_session_id`)

## Testing

```bash
pytest commcare_connect/audit/
```

Mock `LabsRecordAPIClient` and HTTP responses — tests cannot hit production.
