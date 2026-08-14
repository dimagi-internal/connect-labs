# AI Classifier Gateway Retry — Design

**Status:** Approved, implementing.
**Date:** 2026-08-11

## Problem

While testing the KMC Scale [Dial] Classifier on a Bulk Image Audit / Dual-Track
run (`/audit/12761/bulk/?opportunity_id=1487&workflow_run_id=12758`), many
images ended in `ai_result="error"` with the message "Could not reach the AI
classifier service. Try again." Investigation found three compounding causes:

1. `post_with_retry` (`connect_labs/labs/ai_review_agents/base.py`) only
   retries HTTP 429 responses. A genuine connection failure (timeout, DNS,
   connection refused) against the shared classifier gateway raises
   immediately with zero retries, in `scale_dial_validation.py`,
   `scale_validation.py`, `muac_match.py`, and `muac_overzoom.py` alike — all
   four route through the same `post_with_retry` helper and the same generic
   `except httpx.HTTPError` catch.
2. `_run_ai_review_on_sessions` (`connect_labs/audit/tasks.py`) sets
   `session.data["ai_review_complete"] = True` once a session's pass
   finishes, regardless of how many images ended in `"error"`. A later
   re-run of the AI-review task skips the session entirely.
3. The bulk-assessment UI's "Run AI Review" button
   (`connect_labs/templates/audit/bulk_assessment.html`) filters eligible
   assessments with `!a.ai_result` — an `"error"` result counts as set, so
   re-clicking the button doesn't retry it either.

Net effect: an image that hits a real (non-429) gateway failure is a dead
end today, with no automatic or one-click path back to a fresh attempt.

## Scope decisions (confirmed with user)

- Fix applies to all four shared-gateway agents, not just the dial
  classifier — the failure mode lives in shared code.
- Both automatic and manual recovery: automatic for a transient blip,
  manual as a backstop for a sustained outage.
- Automatic recovery has two tiers: extend the existing in-call retry to
  connection errors, AND do one extra batch-level sweep over images that
  still ended in error after the first pass.
- Manual recovery is a per-image "Retry" action on error tiles, not a
  change to the existing bulk "Run AI Review" button's scope.

## Design

### 1. Transport-layer retry — `base.py::post_with_retry`

Extend the existing retry loop to also catch `httpx.TransportError` (the
common base of `ConnectError`, `ConnectTimeout`, `ReadTimeout`,
`PoolTimeout`, etc.) around the `client.post(...)` call, not just inspect
`response.status_code == 429` after a successful post. On a transport
error:

- If attempts remain, wait using the same jittered linear backoff as the
  429 path (there is no `Retry-After` header to honor for a connection
  failure, so it always falls back to `backoff_seconds * (attempt + 1)`
  with jitter) and retry.
- If retries are exhausted, re-raise the last exception so the caller's
  existing `except httpx.HTTPError` in each agent is unchanged and still
  produces `GATEWAY_UNREACHABLE_MESSAGE`.

Because all four agents call this one shared function, this single change
covers `scale_dial_validation`, `scale_validation`, `muac_match`, and
`muac_overzoom` at once. No per-agent edits.

### 2. Task-level second sweep — `tasks.py::_run_ai_review_on_sessions`

After a session's first pass over `work_items` completes (the existing
`ThreadPoolExecutor` loop over `_fetch_and_review`), collect the subset of
items whose `FetchReviewOutcome.ai_result == "error"` (excluding skipped
items) and run `_fetch_and_review` on just that subset once more, before
persisting and setting `ai_review_complete = True`.

- A retry that now succeeds (match/no_match) replaces the original
  `"error"` assessment via `session.set_assessment(...)` and corrects the
  run's `total_errors`/`total_passed`/`total_failed` counters.
- A retry that still errors leaves the original error result in place.
  The session is still marked `ai_review_complete` either way — a
  persistent outage must not loop forever or block the batch, and the
  error remains visible in logs (`total_errors` count, per-image log
  lines) rather than being silently retried indefinitely.
- Exactly one extra sweep, not unbounded. The time already spent
  processing the rest of the batch gives a transient outage room to
  clear; no additional sleep is added before the sweep.

### 3. UI per-tile retry — `bulk_assessment.html`

Add a small "Retry" action on any tile currently showing
`ai_result === 'error'`, following the same visual/interaction pattern as
the existing failed-image-load retry button. It calls the same per-image
endpoint the bulk "Run AI Review" button already uses
(`ExperimentAiReviewView` → `run_single_ai_review_with_notes`) for just
that one assessment, then merges the result through the existing
`updateAssessmentLocal` + autosave flow. No backend change is needed for
this layer — that endpoint already computes a fresh result independent of
`ai_review_complete`.

The existing bulk "Run AI Review" button's eligibility filter
(`!a.ai_result`) is unchanged — it continues to mean "not yet attempted."

### Out of scope

- `classifier_fail_sync.py` and the classifier-fails training-data export
  are unaffected — they only ever recorded `"no_match"` verdicts, never
  `"error"` ones.
- No change to `match`/`no_match` behavior or messaging.
- No alerting/monitoring changes for sustained outages — an outage that
  outlasts both retry tiers still surfaces the same way it does today
  (per-image error tiles + logs), just with more attempts made first.

## Testing plan

- `base.py`: unit test `post_with_retry` retries a transport error and
  succeeds on a later attempt; unit test it re-raises after exhausting
  retries on persistent transport errors.
- `tasks.py`: test a session with one image erroring on the first pass and
  succeeding on the second-sweep retry ends with the corrected result
  persisted and correct counters; test a session where the retry also
  errors still gets marked `ai_review_complete`.
- JS: test the new retry action renders only on `ai_result === 'error'`
  tiles and calls the existing per-image AI-review endpoint for just that
  assessment.
