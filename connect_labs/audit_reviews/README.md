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
| `audit_go_live_run_id`     | First source run that can open reviews; earlier runs are history. None: a preview.   |
| `audit_mode`               | `test` while trying it out.                                                          |
| `audit_setup`              | Registry set-up: `pool`, `parents`, `supporting`, `known`, `registry`.               |
| `audit_program_name`       | Shown on the page.                                                                   |
| `audit_decisions_run_id`   | A run whose state holds decisions saved by the old browser-only page; imported once. |

## API (through the sandbox bridge, `components/workflow/sandbox/routes.ts`)

- `GET  /labs/audit-reviews/api/<workflow>/world/?opportunity_id=<opp>`
- `POST /labs/audit-reviews/api/<workflow>/decisions/` `{"decisions": [{"kind": "open"|"close"|"dismiss", "entity", "status"?, "note"?}]}`
- `POST /labs/audit-reviews/api/<workflow>/events/` `{"review_key", "id", "type": "action"|"comment", "payload"}`

## Tests

- `tests/test_engine_parity.py`: `engine.py` gives the same world as the browser engine it
  replaced (`tests/parity/engine_reference.js`, verbatim) on generated data. Rebuild the
  cases with `node connect_labs/audit_reviews/tests/parity/make_cases.js` only if the
  reference changes. When the rules change on purpose, change `engine.py` and add tests.
- `tests/test_service.py`: sync, import, recompute and the write rules.
- `tests/test_api.py`: who may call the API.
