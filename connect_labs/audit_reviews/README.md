# Audit reviews

Follow-up tickets ("reviews") for the program, LLOs, opportunities and workers that a
weekly indicator report (today: KC Pulse, workflow 27270) shows as yellow or red. Dimagi
staff only.

The page is a workflow (KC Audit reviews, workflow 30017) whose render code asks this app
for everything it shows. The rules run here, not in the browser.

## How it works

1. **Sync** (`service.sync`, on every page load, as the viewer): every completed run of the
   followed report not seen before is frozen as a `SourceRun` (only the 28-day series the
   rules read). Runs the report no longer has are dropped, so a history rebuild is followed.
2. **Recompute** (`service.recompute`): every stored run is replayed through the rules
   (`engine.py`) with every stored `Decision`. The result replaces the `Review` rows and
   the `AuditWorld` payload the page reads. Past weeks are recomputed when the rules change.
   Only staff decisions and `TeamEvent`s (actions, comments) are kept as made.
3. **Decide** (`service.record_decisions`, `service.record_event`): validated against the
   current world (only pending proposals can be confirmed or set aside; "Closed – improved"
   only when ready; "Closed – other" needs a note), then recomputed.

## Setting up a workflow

Everything is on the workflow definition's `config` (no deploy needed):

| Key                        | Meaning                                                                              |
| -------------------------- | ------------------------------------------------------------------------------------ |
| `audit_source_workflow_id` | The report followed. Must also be in `read_workflows`.                               |
| `audit_source_program_id`  | The program that owns the report, when it is not in the audit workflow's opportunity (KC Pulse 30534: program 46). Its runs are listed there, as the viewer. |
| `audit_go_live_run_id`     | First source run that can open reviews; earlier runs are history. None: a preview.   |
| `audit_mode`               | `test` while trying it out.                                                          |
| `audit_setup`              | Registry set-up: `pool`, `parents`, `supporting`, `known`, `registry`.               |
| `audit_program_name`       | Shown on the page.                                                                   |
| `audit_decisions_run_id`   | A run whose state holds decisions saved by the old browser-only page; imported once. |

## API (through the sandbox bridge, `components/workflow/sandbox/routes.ts`)

- `GET  /labs/audit-reviews/api/<workflow>/world/?opportunity_id=<opp>`
- `POST /labs/audit-reviews/api/<workflow>/decisions/` `{"decisions": [{"kind": "open"|"close"|"dismiss", "entity", "status"?, "note"?}]}`
- `POST /labs/audit-reviews/api/<workflow>/events/` `{"review_key", "id", "type": "action"|"comment", "payload"}`
  An action's `payload.attachments` names uploaded documents by id. For `"action": "escalate"` the
  server sets `payload.escalation` (`to`, `to_name`, `to_type`: the review entity's parent) itself;
  the recompute shows it on the parent's open review as an `escalated_in` event.
- `POST /labs/audit-reviews/api/<workflow>/files/?opportunity_id=<opp>&name=<file name>` the body is the
  file (at most 4 MB, the sandbox bridge's limit; documents only, `service.ATTACHMENT_TYPES`) →
  `{"attachment": {id, name, size, type}}`. Stored in Labs' private file store (`Attachment`).
- `GET  /labs/audit-reviews/api/<workflow>/files/<id>/?opportunity_id=<opp>` that document, always as a download.

## Tests

- `tests/test_engine_parity.py`: `engine.py` gives the same world as the browser engine it
  replaced (`tests/parity/engine_reference.js`, verbatim) on generated data. Rebuild the
  cases with `node connect_labs/audit_reviews/tests/parity/make_cases.js` only if the
  reference changes. When the rules change on purpose, change `engine.py` and add tests.
- `tests/test_service.py`: sync, import, recompute and the write rules.
- `tests/test_api.py`: who may call the API.
