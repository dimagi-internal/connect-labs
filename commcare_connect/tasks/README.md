# Tasks App

Task management for FLW follow-ups with timeline events, comments, status tracking, and OCS (Open Chat Studio) bot integration.

This is the simplest example of the labs data_access.py pattern — a good starting point for understanding how labs apps work.

## Key Files

| File             | Purpose                                                                       |
| ---------------- | ----------------------------------------------------------------------------- |
| `models.py`      | `TaskRecord` proxy model with timeline event management                       |
| `data_access.py` | `TaskDataAccess` — LabsRecordAPIClient for state + httpx for Connect/OCS APIs |
| `views.py`       | Task list, create/edit, bulk create, OCS bot integration endpoints            |
| `helpers.py`     | `create_task_from_audit()` — cross-app task creation helper                   |
| `urls.py`        | URL routing under `/tasks/`                                                   |

## Data Model

- **Experiment:** `"tasks"`
- **Type:** `"Task"`
- **Proxy model:** `TaskRecord`

Key properties:

- `title`, `description`, `status`, `priority` (`low` / `medium` / `high`)
- `task_username`, `flw_name` — the FLW this task is about
- `assigned_to_type` (`self` / `network_manager` / `program_manager`), `assigned_to_name`
- `audit_session_id` — optional reference to an audit session
- `events` — timeline array of `{type, actor, description, timestamp, ...}`

Event types: `created`, `updated`, `comment`, `ai_session`, `status_changed`, `assigned`

Status values: `investigating`, `flw_action_in_progress`, `flw_action_completed`, `review_needed`, `closed`

## OCS Integration

Tasks can trigger Open Chat Studio bots for automated FLW outreach:

- `task_initiate_ai()` — triggers bot, creates pending `ai_session` event
- `task_ai_transcript()` — fetches transcript from OCS
- Bot list via `OCSDataAccess` (separate OAuth)

## Cross-App Connections

- **Depends on:** `labs.integrations.connect.api_client`, `labs.integrations.ocs.api_client`
- **Used by:** `workflow/` (creates tasks via `TaskDataAccess`), audit (tasks reference `audit_session_id`)

## Testing

```bash
pytest commcare_connect/tasks/
```

Mock `LabsRecordAPIClient` and OCS HTTP responses.
