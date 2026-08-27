# Performance triage — agent runbook

**Audience: AI agents.** No human runs this. Optimised accordingly — every step is
a copy-pasteable command with an explicit branch condition and a stated
conclusion. Do not narrate; run the command, read the field named, take the
branch.

**One command does steps 1–5 and prints a verdict:**

```bash
python3 tools/perf_triage.py --hours 3          # add --json for machine output
python3 tools/perf_triage.py --hours 168        # the weekly telemetry-review pass
```

Run that first. The sections below are the manual fallback and the reasoning
behind each threshold.

Any window works; the metric period widens automatically past 120h to stay under
CloudWatch's 1440-datapoint cap, and the verdict header prints the period it used.
A wider period averages short spikes away, so **widen to find an incident, then
re-run narrow around it to size it** — a 10-minute saturation that is obvious at
`--hours 3` can read as calm at `--hours 168`.

---

## Facts that change your conclusions — read before diagnosing

| Fact | Consequence |
| --- | --- |
| Web runs `cpu=1024` (**1 vCPU**) with `WEB_CONCURRENCY=3` | 3 threads contend for one core, per task. Measured 2026-08-27: web CPU peaked **90.3%** while average in-flight requests hit **5.6 against 6 slots** (2 tasks x 3) — so the pool is ~93% utilised at ordinary load and a handful of slow requests really does saturate the site. See #1152. |
| `desiredCount=2` (raised from 1 on 2026-07-29) | Losing one task halves capacity but is not an outage. `HealthyHostCount < 1` is. |
| RDS is `db.m6g.large`, ~829 connection slots (resized from `db.t3.small`/~155 on 2026-08-18) | Connection-slot exhaustion is no longer the near-term wall — a 2026-08-27 reading peaked at **42 slots, 5.1%**. Look at web CPU first. |
| Performance Insights is **supported** on `db.m6g.large` but currently **disabled** | It was genuinely unavailable on the old `db.t3.small`, and this runbook used to say "do not propose a PI dashboard". That is no longer true: PI is now an option that has to be turned on, not a dead end. |
| `gunicorn --timeout 600`, ALB `idle_timeout=600` | **Deliberate.** Long-running audit work needs them. Slow requests hang rather than 504 — an inconvenience, not the bug. Never "fix" this. |
| Celery beat runs in the **worker** service only | Web tasks never double-fire scheduled jobs. |
| MCP is `stateless_http=True` | No sticky sessions required; any task can serve any request. |
| `occurred_at` in the audit trail is **flush** time | Never infer latency or ordering from it. `request_id` is the only honest grouping key. |

**The prior that is right most often:** it is the web tier, and the cause is one
request path doing N remote calls. The database is usually innocent — during the
2026-07-29 incident RDS ran at 7–12% CPU with sub-millisecond IO for the whole
54 minutes.

**Timestamp trap.** The AWS CLI returns datapoints in **local** time while
`--start-time`/`--end-time` are **UTC**. Mixing them silently reads the wrong
window and you will draw a confident wrong conclusion. Always pass an explicit
`Z` suffix and read the returned offset.

---

## 1. Alarm state → branch

```bash
aws cloudwatch describe-alarms --profile labs --region us-east-1 \
  --query 'MetricAlarms[?starts_with(AlarmName,`labs-jj`)].{Name:AlarmName,State:StateValue}' \
  --output json
```

| In ALARM | Go to | Conclusion |
| --- | --- | --- |
| `labs-jj-web-cpu-high` | §3 | web tier saturated — the common case |
| `labs-jj-alb-latency-high` only | §3 | slow dependency, not necessarily CPU |
| `labs-jj-rds-connections-high` | §4 | connection pressure |
| `labs-jj-web-no-healthy-targets` | §5 | hard outage — mitigate first |
| `labs-jj-alb-5xx-high` | §3 | ALB cannot get a usable response |
| nothing | §2 | confirm there is a problem at all before digging |

## 2. Is anything actually wrong?

```bash
END=$(date -u +%Y-%m-%dT%H:%M:%SZ); START=$(date -u -v-3H +%Y-%m-%dT%H:%M:%SZ)
for SVC in labs-jj-web labs-jj-worker; do
  echo "=== $SVC ==="
  aws cloudwatch get-metric-statistics --profile labs --region us-east-1 \
    --namespace AWS/ECS --metric-name CPUUtilization \
    --dimensions Name=ClusterName,Value=labs-jj-cluster Name=ServiceName,Value=$SVC \
    --start-time "$START" --end-time "$END" --period 300 --statistics Average \
    --output text | sort -k3 | tail -12
done
```

**Read the web-vs-worker contrast, not the absolute numbers.** It is the single
most diagnostic comparison available and it resolves the biggest fork in one
step:

- web high, worker low → a **request path**. Go to §3.
- worker high, web low → a **Celery task**. Investigate `/ecs/labs-jj-worker`; the site is collateral damage.
- both low → not a compute problem. Go to §4, then §6.

Baseline: median web CPU is **0.8%**. Anything sustained above 90% is real.

## 3. Which request, and why is it expensive

**Primary source — app telemetry.** CloudWatch Logs Insights on
`/ecs/labs-jj-web`:

```
fields @timestamp, path, duration_ms, outbound_calls, db_queries, reason, username
| filter ispresent(outbound_calls)
| sort duration_ms desc
| limit 50
```

Emitted by `connect_labs/utils/request_telemetry.py` for any request over ~3s,
~20 outbound HTTP calls, or ~100 DB queries. Branch on `reason`:

| `reason` | Conclusion | Fix shape |
| --- | --- | --- |
| `outbound_fanout` | one request made N remote calls — **the 2026-07-29 bug** | memoise the resolution, batch into one call, or move off the request path |
| `db_fanout` | N+1 in the ORM | `select_related` / `prefetch_related` |
| `slow` alone | heavy computation or a slow dependency | read the `*_ms` split below before profiling anything |

`outbound_by_host` names the victim. A single request showing
`{"connect.dimagi.com": 139}` **is** a fan-out — no further proof needed.

**For `slow` alone, the counts cannot answer it — the split can.** Every line
carries `outbound_ms`, `db_ms` and `self_ms` (whatever is left after both waits):

| Dominant term | Conclusion | Fix shape |
| --- | --- | --- |
| `outbound_ms` | we are waiting on an upstream, usually Connect | cache/batch the call, or move it off the request path; if Connect itself is slow, that is a Connect bug — file it there |
| `db_ms` | a genuinely expensive query, not an N+1 (the count would be low) | read the plan; index or narrow it |
| `self_ms` | our own Python — **but see the caveat below before you believe it** | profile the view |

This split exists because 2026-08-11 hit a shape the counts alone could not
resolve: `/audit/api/<id>/bulk-data/` at 9–87s on **2** outbound calls and ~16
queries. Too few calls for `outbound_fanout`, too few queries for `db_fanout`,
and nothing recorded said where the seconds went. Read these three before
forming a hypothesis; they are cheaper than being wrong.

### `self_ms` is a REMAINDER, not a measurement — check the clients first

`self_ms` is computed as `duration_ms - outbound_ms - db_ms`. It is not observed;
it is whatever the other two failed to account for. So an outbound call made by a
client that does **not** report itself does not merely go uncounted — it is
silently reclassified as our own Python, and this table then sends you to profile
a view that is sitting on a socket.

Counting is opt-in. A client is instrumented only if it was built with
`event_hooks=request_telemetry.httpx_event_hooks()`, or calls
`request_telemetry.record_outbound_call()` itself. **A bare `httpx.get(...)` takes
no hooks and can never be counted.** Before trusting a dominant `self_ms`:

```bash
grep -rn "httpx.Client(\|httpx.get(\|httpx.post(" --include="*.py" connect_labs/ \
  | grep -v request_telemetry
```

and confirm every client on the path in question reports itself.

The cheap cross-check that does not depend on any of this: **httpx logs every
request at INFO**, so the web log has the ground truth regardless of who opted in.
Pull the raw lines for the request's own time window and count them.

*Why this is here:* on 2026-08-26 `/labs/workflow/<id>/run/` logged
`outbound_calls: 0, outbound_ms: 0, self_ms: 15832` — a textbook "our own Python"
read. It was making **22 sequential Google Drive round-trips**. `DriveClient` used
the module-level `httpx.get` and `ExportAPIClient` had omitted the hooks; only
`api_client.py` had opted in, so two entire integrations were invisible and every
second they spent waiting was booked to us. Both report themselves as of #1300 —
but the general trap outlives that fix, because the next client added will not be
instrumented either unless someone remembers. `outbound_calls: 0` means *nothing
that opted in made a call*; it does not mean no call was made.

**If that returns nothing**, the app was too saturated to log, or the telemetry
predates the request. Fall back to ALB access logs, recorded outside the process:

```bash
aws s3 cp s3://labs-jj-alb-access-logs/alb/AWSLogs/858923557655/elasticloadbalancing/us-east-1/$(date -u +%Y/%m/%d)/ \
  ./alblogs --recursive --profile labs --exclude "*" --include "*.log.gz"
gunzip -rf ./alblogs
python3 - <<'PY'
import glob, collections
rows=[]
for f in glob.glob("./alblogs/**/*.log", recursive=True):
    for line in open(f):
        p=line.split()
        if len(p) < 14: continue
        try: rows.append((float(p[6]), p[8], p[13].strip('"').split('?')[0]))
        except (ValueError, IndexError): pass
agg=collections.defaultdict(list)
for t,s,u in rows: agg[u].append(t)
for u,ts in sorted(agg.items(), key=lambda kv:-sum(kv[1]))[:10]:
    print(f"total={sum(ts):8.2f}s n={len(ts):>5} avg={sum(ts)/len(ts):6.3f}s {u}")
PY
```

Rank by **total time consumed**, not by slowest single request: a 0.3s endpoint
called 5,000 times hurts more than one 40s request, and only the total exposes it.

## 4. Database

Check it to **rule it out**, and expect to. Move on quickly unless connections
are the thing moving.

```bash
END=$(date -u +%Y-%m-%dT%H:%M:%SZ); START=$(date -u -v-3H +%Y-%m-%dT%H:%M:%SZ)
for M in CPUUtilization DatabaseConnections ReadLatency WriteLatency; do
  echo "=== $M ==="
  aws cloudwatch get-metric-statistics --profile labs --region us-east-1 \
    --namespace AWS/RDS --metric-name $M \
    --dimensions Name=DBInstanceIdentifier,Value=labs-jj-postgres \
    --start-time "$START" --end-time "$END" --period 300 --statistics Average Maximum \
    --output text | sort -k3 | tail -8
done
```

Baselines: CPU 7–12%, read/write latency sub-millisecond, connections 5–15
against ~155 slots.

**Connections are a symptom, not a cause.** Sync Django views under ASGI each
hold a connection from the asgiref thread pool, so a request pile-up shows here
first. High connections + low DB CPU = slow requests holding connections. Fix the
requests (§3), not the database. Only conclude "database" if CPU or IO latency is
also elevated.

## 5. Did something ship?

```bash
aws ecs describe-services --profile labs --region us-east-1 \
  --cluster labs-jj-cluster --services labs-jj-web \
  --query 'services[0].events[0:10].[createdAt,message]' --output text
git log --oneline --since="6 hours ago"
```

Failure starting at a deploy boundary = regression until disproven. Inverse rule:
an abrupt **drop** in CPU or connections with **no** ECS event is a process
recycle or the workload ending — not evidence your fix worked.

## 6. Mitigate, then fix

```bash
# Add capacity (buys time; does not fix a fan-out)
aws ecs update-service --profile labs --region us-east-1 \
  --cluster labs-jj-cluster --service labs-jj-web --desired-count 4

# Recycle a wedged task
aws ecs update-service --profile labs --region us-east-1 \
  --cluster labs-jj-cluster --service labs-jj-web --force-new-deployment
```

Each extra task costs **$42.53/month** (1 vCPU + 4 GB Fargate, us-east-1).

**Scaling is not a fix for a fan-out.** N remote calls per request stays N; you
have only bought parallelism, and you have also raised the ceiling on concurrent
DB connections against a ~155-slot instance. Fix the request path.

## 7. Close the loop

Record the review as an audit event (`POST /labs/audit-trail/`) naming what was
checked. §164.308(a)(1)(ii)(D) requires the activity review to be *practised*,
and the review itself is an auditable event.

---

## Anti-patterns — each one has cost real time

- **Starting at the database.** It is usually healthy. §2 first.
- **Trusting Average latency.** Dragged to ~0 by health checks and static assets; it stayed unremarkable through a total outage. Use **p95**.
- **Watching only target 5xx.** During the incident target 5xx was `1` while ELB-generated 5xx peaked at `84`. ELB 5xx means the ALB got no usable response — that is the saturation signal.
- **Lowering the timeouts.** See the facts table. They are load-bearing.
- **Quoting raw `data_update` counts as usage.** Background `workflow_run` churn dominates. Filter to `source=web` with a username, or use Umami `pageviews`/`visitors`.
- **Concluding from one CloudWatch page.** `filter-log-events` paginates; a truncated read looks like a quiet system. Check for `nextToken` before believing a low count.
- **Reporting a fix as verified because the metric recovered.** Confirm the mechanism changed, not just that load stopped.
