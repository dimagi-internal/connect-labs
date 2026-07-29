# Performance runbook — "labs is slow"

Written after the 2026-07-29 incident, which took far longer to diagnose than it
should have. The order below is deliberately the order that would have found it
fastest. Work top-down and stop when a step gives you a culprit.

**The single most useful prior:** labs runs **one** web task on **one** vCPU
(`desiredCount=1`, `cpu=1024`, `WEB_CONCURRENCY=3`). There is no horizontal
capacity to absorb anything. One expensive request path saturates the whole
site for everyone, and that is the most likely explanation for "labs is slow".

---

## 0. Is it actually the app? (30 seconds)

```bash
aws cloudwatch describe-alarms --profile labs --region us-east-1 \
  --query 'MetricAlarms[?starts_with(AlarmName,`labs-jj`)].{Name:AlarmName,State:StateValue}' \
  --output table
```

`labs-jj-web-cpu-high` in ALARM means the web tier is saturated — go to §2.
`labs-jj-rds-connections-high` alone means the database — go to §3.

## 1. Confirm the shape (2 minutes)

Compare **web** CPU against **worker** CPU over the window. This one comparison
told the whole story on 2026-07-29: web pinned at 100% for 54 minutes while the
Celery worker idled below 1%, which immediately ruled out background jobs.

```bash
for SVC in labs-jj-web labs-jj-worker; do
  echo "=== $SVC ==="
  aws cloudwatch get-metric-statistics --profile labs --region us-east-1 \
    --namespace AWS/ECS --metric-name CPUUtilization \
    --dimensions Name=ClusterName,Value=labs-jj-cluster Name=ServiceName,Value=$SVC \
    --start-time "$(date -u -v-3H +%Y-%m-%dT%H:%M:%SZ)" \
    --end-time "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --period 300 --statistics Average --output text | sort -k3
done
```

> **Careful with timestamps.** The CLI returns datapoints in **local** time while
> `--start-time`/`--end-time` are UTC. Mixing them silently reads the wrong
> window — always pass an explicit `Z` suffix and read the returned offsets.

## 2. Which request is expensive?

**Start here — this is the step that did not exist before.**

```
fields @timestamp, path, duration_ms, outbound_calls, db_queries, reason, username
| filter ispresent(outbound_calls)
| sort duration_ms desc
| limit 50
```

Run against `/ecs/labs-jj-web` in CloudWatch Logs Insights. Emitted by
`connect_labs/utils/request_telemetry.py` for any request over ~3s, over ~20
outbound HTTP calls, or over ~100 DB queries.

Read the `reason` field:

| `reason` | Means | Usual cause |
| --- | --- | --- |
| `outbound_fanout` | one request made many remote calls | a loop calling a remote API per item — **the 2026-07-29 bug** |
| `db_fanout` | many SQL queries in one request | a classic N+1; missing `select_related` / `prefetch_related` |
| `slow` alone | slow without either | a genuinely heavy computation, or a slow remote dependency |

`outbound_by_host` names who is being hammered. A single request showing
`{"connect.dimagi.com": 139}` is a fan-out, full stop.

If the app was too saturated to log, fall back to the ALB access logs, which are
recorded outside the process (§ `infra/README.md`).

## 3. Is it the database?

Usually **no** — check before spending time here. Throughout the 2026-07-29
outage RDS ran at 7–12% CPU with sub-millisecond IO. Anyone who started at the
database found nothing wrong and lost an hour.

```bash
for M in CPUUtilization DatabaseConnections ReadLatency WriteLatency; do
  echo "=== $M ==="
  aws cloudwatch get-metric-statistics --profile labs --region us-east-1 \
    --namespace AWS/RDS --metric-name $M \
    --dimensions Name=DBInstanceIdentifier,Value=labs-jj-postgres \
    --start-time "$(date -u -v-3H +%Y-%m-%dT%H:%M:%SZ)" \
    --end-time "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --period 300 --statistics Average Maximum --output text | sort -k3 | tail -12
done
```

Connections are the metric that actually moves. Baseline is 5–15; the instance
has roughly **155** usable slots. Sync Django views under ASGI each take a
connection from the asgiref thread pool, so a request pile-up shows up here
first — 106 during the incident, purely as a *symptom* of slow requests holding
connections, not as a cause.

Note **Performance Insights is unavailable** on `db.t3.small`. Confirm the class
before promising anyone a PI dashboard.

## 4. Did something just ship?

```bash
aws ecs describe-services --profile labs --region us-east-1 \
  --cluster labs-jj-cluster --services labs-jj-web \
  --query 'services[0].events[0:10].[createdAt,message]' --output text
git log --oneline --since="4 hours ago"
```

A failure that starts at a deploy boundary is a regression until proven
otherwise. Note the reverse too: an abrupt **drop** in connections or CPU with
no ECS event is a process recycle, not a fix.

## 5. Mitigate, then fix

Buying room, in increasing order of disruption:

```bash
# Restart the web task — clears a wedged process. Brief outage: desiredCount=1.
aws ecs update-service --profile labs --region us-east-1 \
  --cluster labs-jj-cluster --service labs-jj-web --force-new-deployment

# Or add capacity, which for a CPU-bound fan-out only buys time.
aws ecs update-service --profile labs --region us-east-1 \
  --cluster labs-jj-cluster --service labs-jj-web --desired-count 2
```

Then fix the cause. For a fan-out the fix is almost never "scale up" — it is to
stop making N calls: memoise the resolution, batch it into one call, or move the
work off the request path into Celery. See `AuditDataAccess.get_audit_session`
for a worked example of all three considerations.

---

## Things that will mislead you

- **Do not lower the timeouts.** `gunicorn --timeout 600` and the ALB
  `idle_timeout=600` are deliberate — long-running audit work needs them. They
  are why slow requests hang rather than 504, which is a diagnostic
  inconvenience, not a bug.
- **Average latency lies.** It is dragged toward zero by health checks and
  static assets and stayed unremarkable through the entire incident. Alarm and
  investigate on **p95**.
- **Target 5xx vs ELB 5xx are different failures.** During the incident target
  5xx was `1` while ELB-generated 5xx peaked at `84`. Watching only application
  errors misses saturation entirely.
- **`occurred_at` in the audit trail is flush time, not event time.** Never
  infer latency from it; `request_id` is the only honest grouping key.
- **Audit-trail events are mostly machine traffic.** Background `workflow_run`
  churn dominates `data_update`, so raw event counts are not usage. Filter to
  `source=web` with a username before saying anything about real users.
