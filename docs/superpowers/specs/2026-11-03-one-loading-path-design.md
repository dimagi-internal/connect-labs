# One way to load report data

Status: live since #2816. Follows #2814 and the CPU page in #2810.

## The problem

Labs grew four ways to wait for slow data:

| Path | Where the work ran | How the viewer saw it |
|---|---|---|
| Pipeline stream (`pipeline-data/stream`) | Web request | SSE, runner strip |
| Pipeline rows stream (`pipeline-rows/stream`) | Web request | SSE, painted by each render |
| Pipeline rows and data JSON | Web request | Nothing |
| 202 polling (`cache_warm.py`) | Worker | Host strip |

Celery job progress (`streamTaskProgress`) is a fifth, for work a person starts
rather than data a page reads. It already polls, and it owns the progress shape.

The web tier has one vCPU per task, so every path that fills in a request can do what
paged us on 2026-11-03. And each path says "loading" differently.

## The rule

**A read answers from the cache, or it says a fill is under way. It never fills.**

```
reader     GET the data
server     cached?  200 with the data
           cold?    claim the slot, queue one worker task, 202 with progress
reader     wait retry_after_ms, ask again
worker     fill the cache, report progress, leave an outcome
```

One of each:

- **Fill:** `WarmSlot` (`cache_warm.py`). Viewers of the same data share one task.
- **Plans:** a fill is `warm_pipelines` (named pipelines, named opportunities) or
  `warm_visit_cache` (hold every slot the semantic layer reads, for 90 minutes). Those
  are different jobs with different cost, so they stay two tasks in one slot protocol.
- **Progress shape:** `TaskProgress`, translated by the existing `build_task_progress`,
  so a 202 and a Celery job say the same thing in the same fields.
- **Transport:** polling, the repo's stated preference since #856. A held stream pins
  a thread and dies with a deploy; a poll does neither. The ALB idle timeout is 600 s,
  so a big JSON answer still gets through.
- **Client loop:** `pollWhileWarming` (`warming.ts`), used by the runner and the
  sandbox host.
- **Strip:** `LoadingStrip`, for the runner and the sandbox frame.

## What happens to each path

- `pipeline-data/` becomes the page's read. The runner polls it instead of opening an
  EventSource.
- `pipeline-rows/` and `pipeline-query/` answer from the cache or 202, and share a
  slot per (opportunity, pipeline), so a drill joins a fill another drill started.
- `pipeline-data/stream` and `pipeline-rows/stream` stay, because forked live copies
  and two templates still open them. They become relays over the same read: they wait
  on the same slot, turn progress into the events those renders already understand,
  and send the same final payload. They hold no work.
- `streamTaskProgress` is unchanged. Its shape is the one everything else adopts.

## The cache rule cannot fork

`AnalysisPipeline.read_cached` is the cache-first branch of `stream_analysis`, pulled
out so both use it. A cheaper "is it cached?" check beside it would eventually disagree
and a reader would poll forever, so a read just tries to read.

## What a fill must carry

A worker has no browser session, so the task carries what the session supplied, or a
read that follows the fill would disagree with it:

- the viewer's id: the worker finds their CommCare and OCS tokens from it
- the opportunity's visit count, which `read_cached` validates against
- `refresh`, `accept_low_count` and `tolerance`

## Ending the wait

A fill can finish and leave a target cold: an export failed, or there are no rows. A
reader that warmed again would poll forever, so a finished slot leaves an outcome, and
a reader that finds it answers rather than filling again:

- Per-target results travel in the outcome: errors, `auth_error`, `raw_fetch_anomaly`,
  `concurrent_run`. The reader merges them into the metadata renders already read.
- A target with no error that the read still calls cold is served with `read_cached`'s
  count check off (`get_cached_result_only`). A visit count that drifted must not read
  as an empty report.
- `refresh=1` is satisfied only by a fill that was itself forced.
- A plain reader during a forced fill is served from the old cache while it can. Once one finds a pipeline cold, polls wait without reading, for the rest of that fill.
- Only the visits export is validated against the visit count. Other sources' rows are not visits, so they rely on `expires_at`.

## Limits

- A cold open takes as long as the download. This moves the load and shows it.
- A warm read of a big report still builds its JSON in the web request. This covers
  fills, not payload size. `noPipelineStream` remains the answer to payload size.
- The worker takes all of it: 1 vCPU, concurrency 6. A big walk slows other Celery
  tasks. Check worker CPU after this ships, and split a queue if it hurts.
- `warm_visit_cache`'s raw download reports a stage, not a row count, because
  `fetch_raw_visits` has no progress hook.
- Redis down takes out the broker and the slots together. A cold read then fails
  instead of filling in the request. That is deliberate: the alternative is the page.
