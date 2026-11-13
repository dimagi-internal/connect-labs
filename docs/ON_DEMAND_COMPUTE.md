# On-demand compute: running an expensive model from Labs

For a model that needs special software or hardware and takes minutes to run (a simulation, a large optimisation),
so it cannot run inside a web request or on the shared Celery worker. Labs has built one: a live malaria
simulation (IDM's EMOD) behind the PMC explorer, retired on 13 Nov 2026 and kept at the git tag
`archive/pmc-emod` as the worked example. This page is the pattern it taught us, and the one a second
consumer should follow. **No live consumer exists today.**

## The shape

A request becomes a durable job; the job borrows a machine; the answer is cached by the request's hash.

| Part | What it is | Where it lives |
|---|---|---|
| **Request** | Plain JSON describing the question, *content-addressed*: `sha256` of the canonical JSON. The same question twice finds the first answer instead of paying for a second run. | the consumer's `build_request` / `request_hash` |
| **Job** | A long-running MCP tool (`register(long_running=True)`): the call returns `{job_id, state}` or, after a short wait, the result. The generic `job_status` tool follows it. It survives a Labs deploy. | `mcp/jobs.py`, `mcp/tool_registry.py`, `mcp/tools/jobs.py` |
| **Executor** | `canopy_sdk.ondemand.OnDemandInstance`: an EC2 box that is **stopped while idle**, started when a job needs it, driven over SSM. A `Lease` (Redis) lets only one run own the box. | the consumer's `runner.py`; the stack in `deploy/aws/` |
| **Result cache** | `results/<hash>.json` in the stack's artifacts bucket, read back by the job. The job is safe to rerun from the start, so a rerun is recovery. | S3 |
| **Surface** | The tool, and (if a page needs it) an API that submits the same request and polls the same job. The page and the agent ask the same question the same way. | the consumer's views and tools |

Build the consumer on the generic job. **Do not rebuild what the job already gives you.** The EMOD runner predates
`mcp/jobs.py` and carried its own copy of it: a `PmcModelRun` table, a heartbeat that touched the row every 60 s, a
sweep that re-queued dead runs, its own Celery tasks, and its own run and status tools. `MCPJob` does all of that
(durable across deploys via `acks_late`, a claimed lease with a heartbeat, bounded attempts, a privacy-gated
`job_status`, `MCP_JOBS_RUN_INLINE` for tests), so a new consumer needs only the request hash, the executor and the
result cache.

## What to carry over from the EMOD design

- **Content-address the request, and make the handler idempotent.** Check the result cache first; a job that runs
  twice must write the same answer twice.
- **Bound the load.** The EMOD service allowed three runs in flight (one running, two waiting) and answered `429`
  beyond that, so at most three of the Celery pool's six slots were ever held by waiting work. Pick the bound
  from the pool size, and say it.
- **A short lease, refreshed by a heartbeat.** The box's lease had a 300 s TTL refreshed every 60 s, so a lease
  orphaned by a killed worker freed itself in five minutes. A long TTL turns one crash into an outage.
- **Recover from the result, not only from the row.** The instance writes `results/<hash>.json` whether or not
  anyone is waiting, so a run whose task a deploy killed was finished from S3 on the next poll.
- **Three layers of idle shutdown**, so a forgotten box costs one hour, not a month: the consumer's own `stop()`,
  an in-VM watchdog (halts after an hour without a touch of the activity marker), and a CloudWatch alarm on low CPU.
  Match the alarm window to the watchdog's, or the box stops between questions.
- **Say how long it will take, honestly.** Record at submit whether the box was warm or cold and quote that, rather
  than a ticking countdown. Measured: about two minutes warm, five cold. Pass a boot timeout that fits (the SDK's
  default 180 s was too close to a cold start).
- **Tell visitors one public sentence; keep the detail for admins.** The row held the SDK's message (instance ids,
  SSM output); the page showed "The live model is unavailable right now."
- **Bucket layout with different lifetimes.** `requests/` and `results/` expire after a week; `worker/` (the install
  source) and any cached burn-ins never expire; abort incomplete multipart uploads after a day.
- **The box finds its own bucket by tag.** The stack creates the bucket under a generated name, so the instance is
  tagged `artifacts-bucket=<name>`, instance metadata tags are enabled, and the first-boot script reads the tag.
  First boot runs from `per-boot/` so a stop mid-install retries.

## What to do differently

1. **Use `MCPJob`** (above). Choose `job_runner="worker"` for minutes, `"standalone"` (its own Fargate task) for an hour or more.
2. **Treat the CloudFormation template as a pinned copy with a checklist.** CloudFormation cannot import from a
   Python wheel, so the stack was the SDK's template copied with **ten marked edits** (defaults, outbound port 80
   for a first-boot install, `s3:GetObject` for the instance role, instance metadata tags and the bucket tag, consumer
   `PutObject` on `requests/*`, a launch-template version CloudFormation accepts, per-prefix lifecycle, an idle-window
   parameter, a narrowed instance `PutObject`). Each edit is a reason the SDK default did not fit. Keep that list next
   to the copy, re-apply it on every SDK bump, and prefer to propose the edits upstream as parameters.
3. **Say what the model fixes and what it does not**, on the page and in the tool: the EMOD runs were illustrative,
   fitted per state, deaths only. The consumer's honesty fields belong in the request/response contract, not in prose.

## Adding a consumer, in order

1. Define the request JSON and its hash; define the result JSON and what a reader must be told about it.
2. Write the worker (a script run over SSM that reads `requests/<hash>.json` and writes `results/<hash>.json`) and
   its installer; keep both in the repo.
3. Deploy the stack (copy the template, apply the edits, set `Capability`, the instance type and the bucket lifecycle).
4. Register the tool with `long_running=True`; the handler checks the cache, takes the lease, runs, writes, returns.
5. Add the page or API only if people need it; have it submit the same request and poll `job_status`.
6. Test with `MCP_JOBS_RUN_INLINE` and a fake instance; test that a rerun after a kill returns the cached result.
7. Document the stack in `deploy/aws/` and add the consumer to the table below.

## Consumers

| Consumer | Status | Code |
|---|---|---|
| Malaria PMC schedules (IDM EMOD) | Retired 13 Nov 2026 | tag `archive/pmc-emod`: `labs/indicators/emod/`, `tools/pmc_emod/`, `deploy/aws/` |

The retirement removed the code, the `labs_pmc_model_run` table (migration `0015_delete_pmc_model_run`; it held only
cached run results) and the six `targeting_pmc_*` tools. **The AWS stack `labs-emod` (instance, artifacts bucket,
idle alarm) was not touched.** Decommission it or reuse it for the next consumer; either is a decision for whoever
owns the account.
