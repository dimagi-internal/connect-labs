# After deploying "Highlight FLW's name the number of times they have been flagged for any number of duplicates"

The bulk audit review screen gained a per-FLW duplicate/fake history strip. It reads
`PriorAuditVerdict.username` and `.visit_date`, two columns added by
`audit/0006_prior_audit_flw_and_visit_date`.

**The migration cannot fill them.** Their values come from each audit session's
`data["visit_images"]` blob, which lives in Connect, not in the labs database — a
migration has no API client and no user token to go and fetch them. `AddField` therefore
leaves every pre-existing row at `username=''` / `visit_date=NULL`.

Production already holds a built projection (~17,653 rows across 712 completed sessions
on opportunity 2154 as of 2026-08-27), so unlike a fresh local database it will **not**
rebuild itself: `_projection_can_serve` sees a current state row and serves the legacy,
unattributed rows straight from the table.

## What you will see before the backfill

The panel reads:

> History unavailable — the prior-audit index has no FLW attribution yet.
> Run `rebuild_prior_audit_index` to populate it.

This is deliberate. `prior_audit_projection.has_flw_attribution()` detects the condition
and refuses to render an empty strip, because an empty strip means "this FLW has never
been flagged" — which would be false for every FLW with any history. Nothing else on the
review screen is affected: the images, verdicts, filters and saves all work normally.

## Steps

Run once per opportunity in program 217, via the `run-labs-command.yml` GitHub Action.

1. **Dry-run one opportunity first.** `--verify-only` diffs the projection against the
   live computation and exits non-zero on disagreement, so it can catch a problem before
   anything is written:

   ```
   rebuild_prior_audit_index --opportunity 2154 --verify-only --as <labs-username>
   ```

2. **Backfill each opportunity:**

   ```
   rebuild_prior_audit_index --opportunity 2154 --as <labs-username>
   rebuild_prior_audit_index --opportunity 2155 --as <labs-username>
   rebuild_prior_audit_index --opportunity 2156 --as <labs-username>
   rebuild_prior_audit_index --opportunity 2157 --as <labs-username>
   ```

3. **Confirm.** Open any audit in that opportunity — the strip should replace the
   "History unavailable" line. `--json` on a rebuild reports the session and row counts
   it wrote if you want the numbers instead.

### Rules for the credential

- **Use `--as <labs-username>`, never `--token`.** The Action takes its command line as a
  free-text dispatch input, so a raw token would be recorded in the workflow inputs and
  the Actions log. `--as` looks up that user's stored Connect token and keeps the
  credential in the database where it already lives.
- **Prefer the widest-scoped labs user available.** Connect's export API returns only
  what the calling identity's org membership can see, so a narrow identity simply builds
  less. This is safe rather than destructive — `rebuild_opportunity` merges and cannot
  delete rows for sessions it did not see (#1260) — but a narrow run leaves gaps that
  look like clean history.

## Why this is not automatic

`populate()` already refreshes rows for every session it sees, so a missing-attribution
read could have triggered a rebuild by itself. It deliberately does not, because
`PRIOR_AUDIT_ASYNC_STALE_REFRESH` is unset in deploy config and therefore `False`: a
rebuild triggered from a read runs **synchronously inside that request**. On opportunity
2154 that means one auditor waiting on a fetch of ~712 completed sessions from Connect
mid-page-load — the cost profile of the 2026-08-20 web-tier saturation (#1246, #1152)
that this projection was introduced to remove. A command run deliberately, off the
request path, at a chosen time, is the better trade.

Two alternatives exist if the manual step becomes unwelcome: enable
`PRIOR_AUDIT_ASYNC_STALE_REFRESH` so the rebuild is handed to a worker (#1360), or stamp
a row-version on `PriorAuditProjectionState` so the first read triggers exactly one
rebuild and cannot loop. Neither is implemented.

## No backfill needed after this

Newly completed audits write both fields as they go, via `rows_for_session`. This is a
one-time cost of introducing the columns, not a recurring maintenance task. A future
`rebuild_prior_audit_index` run for any other reason is harmless and idempotent.
