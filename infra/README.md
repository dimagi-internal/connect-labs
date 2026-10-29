# Labs infrastructure-as-code

This directory holds CloudFormation templates for the connect-labs AWS
environment (`us-east-1`, profile `labs`). The approach mirrors the sibling
**`dimagi-rad/scout`** project (`infra/scout-stack.yml`): plain AWS
CloudFormation — no Terraform state backend to manage, native to the account,
reviewable in a PR.

## Why this exists

The labs AWS infra (ECS Fargate cluster + services, RDS, ElastiCache, ALB, IAM)
was originally created click-ops / ad-hoc CLI. That worked until the
2026-06-29 RDS connection-leak incident, where the **complete absence of
alarms** meant a connection climb went unnoticed until it caused a site-wide
outage (see PR #765 for the leak fix). This directory is the start of bringing
the **operational guardrail layer** under version control so it is reviewed,
discoverable, and reproducible.

## Scope (intentionally incremental)

`labs-monitoring.yml` is a **standalone** stack. It does **not** own the core
infra — the RDS instance, ECS cluster/services, ALB, redis, and IAM roles are
still managed out-of-band and are merely **referenced** here by name/id. That
keeps this first slice safe to create/update/delete without touching running
resources.

Bringing the core resources under CloudFormation (importing the existing RDS,
ECS, etc.) is a deliberate later step — "the rest, as needed" — and only worth
doing if labs proves long-lived enough to justify the import work.

| Template                     | Owns                                                                                                                                                                                                                                                                                                                                                                                                                                                 | References (does not own)                                                                                            |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `labs-monitoring.yml`        | SNS alert topic + subscriptions, RDS-connection + slot-exhaustion + free-storage alarms, web-CPU / ALB-latency / ALB-5xx / target-5xx / no-healthy-target alarms, AI-review empty-audit + classifier-saturation alarms, log metric filters, CI log-read IAM policy                                                                                                                                                                                   | RDS instance, ECS cluster + service, ALB + target group, ECS log groups, GitHub Actions deploy role                  |
| `labs-tenant-monitoring.yml` | canopy-web + ace-web per-target-group application-5xx alarms                                                                                                                                                                                                                                                                                                                                                                                         | The ALB, both tenant target groups, the alert topic (imported from `labs-monitoring.yml`'s export)                   |
| `labs-audit-analytics.yml`   | Umami service (log group, target group, `/umami/*` ALB rule, task def, ECS service), Umami CodeBuild image pipeline + its role, audit-archive/secrets IAM inline policies                                                                                                                                                                                                                                                                            | Object-Locked audit S3 bucket, Umami secrets, ECR repo, ALB/cluster/roles/VPC                                        |
| `labs-access-logs.yml`       | ALB access-log S3 bucket, its delivery policy, and a 90-day retention lifecycle                                                                                                                                                                                                                                                                                                                                                                      | The ALB itself (logging is switched on via a CLI attribute — see below)                                              |
| `labs-email.yml`             | SES domain identity + DKIM, `labs-jj-email` configuration set, `labs-jj-email-events` SNS topic + event destination, scoped `ses:SendEmail` managed policy                                                                                                                                                                                                                                                                                           | ECS task role (policy attaches by name), the DNS zone, SES production access                                         |
| `labs-observe-readonly.yml`  | `labs-jj-observe-readonly` role + managed policy: read-only, non-sensitive observability (metrics, alarms, ALB/ECS/RDS/ElastiCache/stack metadata) with explicit denies on log events, object bodies, secrets, task-definition env and DB data                                                                                                                                                                                                       | canopy runner instance roles + canopy-web's task role (they need their own `sts:AssumeRole` grant), Identity Center  |
| `labs-media.yml`             | `labs-media-uploads` inline policy: Get/Put on `media/*` in the exports bucket, where `default_storage` (supply documents) writes                                                                                                                                                                                                                                                                                                                    | ECS task role, the exports bucket (private; its ListBucket grant is out-of-band)                                     |
| `labs-overture.yml`          | `labs-overture-snapshots` inline policy: Delete on `overture/snapshots/*` in the exports bucket, so `overture_refresh` can prune Overture snapshots older than the rollback (Get/Put/List are out-of-band)                                                                                                                                                                                                                                           | ECS task role, the exports bucket                                                                                    |
| `labs-synthetic-jobs.yml`    | `labs-synthetic-jobs` inline policy: `ecs:RunTask` from the worker family on the labs cluster, `ListTasks`/`StopTask`/`DescribeTasks`/`DescribeServices`/`DescribeTaskDefinition`, and `iam:PassRole` for the task + execution roles -- so each synthetic job runs on a standalone Fargate task a deploy does not stop (`labs/synthetic/ecs_runner.py`); the `labs-jj-synthetic-job-overran` and `-machine-died` alarms and their log metric filters | ECS task role, execution role, the cluster, the worker log group, the alert topic (`labs-jj-alert-topic-arn` export) |

## Deploy

```bash
aws cloudformation deploy \
  --region us-east-1 --profile labs \
  --stack-name labs-jj-monitoring \
  --template-file infra/labs-monitoring.yml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides AlarmEmail=you@dimagi.com
```

- **This template is at CloudFormation's inline size limit — check
  `wc -c infra/labs-monitoring.yml` before adding to it.** CloudFormation accepts
  at most **51,200 bytes** of inline template body (`--template-file` /
  `--template-body`); after the 2026-09-09 change the file is 51,076. Over the line the deploy
  fails with `Member must have length less than or equal to 51200`, which reads
  like a malformed template and is only a size limit. The escape is
  `--s3-bucket <bucket> --s3-prefix labs-monitoring` (the CLI uploads and passes a
  URL; limit 1 MB) — which needs a bucket this account does not yet have, so the
  2026-09-09 tenant alarms went into their own template instead (below). When the
  file next crosses the line, create the bucket (private, block public access,
  30-day expiry — it belongs in the bootstrap stack of the labs IaC split,
  canopy-web #729) and add the flag here.
- Omit `AlarmEmail` (or pass empty) to create the alarms + SNS topic without an
  email subscription — alarms still fire to the topic; wire Slack/another
  endpoint to the exported `labs-jj-alert-topic-arn` later.
- After the first deploy with an email, **confirm the subscription** via the
  email AWS sends, or alarms won't reach your inbox.
- Re-run the same command to apply template changes (idempotent).
- **Carry existing parameters forward, or a re-deploy will silently reset them
  to their defaults.** `AlarmEmail` defaults to `''`, and the email subscription
  is conditional on it, so a deploy that does not pass the current value
  _removes the subscription_ — the alarms stay up and simply stop reaching
  anyone. This is not hypothetical: a change set on 2026-08-18 that only meant
  to add two alarms came back with `Remove AlertEmailSubscription`, because the
  live stack had `AlarmEmail=connect-labs-alerts@dimagi.com` and the default is
  blank. Always review a change set before executing, and carry values forward:

  ```bash
  aws cloudformation describe-stacks --stack-name labs-jj-monitoring \
    --profile labs --query 'Stacks[0].Parameters[].ParameterKey' --output text \
    | tr '\t' '\n' | sed 's/^/ParameterKey=/;s/$/,UsePreviousValue=true/' > /tmp/params.txt

  aws cloudformation create-change-set --profile labs \
    --stack-name labs-jj-monitoring --change-set-name my-change \
    --template-body file://infra/labs-monitoring.yml \
    --capabilities CAPABILITY_NAMED_IAM \
    --parameters $(tr '\n' ' ' < /tmp/params.txt)

  # review, then:
  aws cloudformation execute-change-set --profile labs \
    --stack-name labs-jj-monitoring --change-set-name my-change
  ```

- **Verify a new metric filter actually matches**, since one that matches nothing
  produces an alarm that can never fire and looks identical to a healthy one:

  ```bash
  aws logs test-metric-filter --profile labs \
    --filter-pattern '"[classifier]" "outcome=timeout"' \
    --log-event-messages '<a real line that should match>' '<one that should not>'
  ```

### An SNS subscription nobody confirms is the same as no alerting

Until 2026-07-29 this topic had **zero confirmed subscribers**: `AlarmEmail` was
set to a Google Group, and the only other subscription was `Deleted`. Every
alarm was firing into nothing.

A Google Group cannot complete an SNS subscription. The confirmation is a link
someone has to click, and the group's default posting policy rejects mail from
an external sender (AWS SNS), so the confirmation usually never arrives — and
when it does, no member owns clicking it. The link expires after 3 days.

`AgentAlarmEmail` (default `hal@dimagi-ai.com`) is the fix: an agent mailbox
reads its own inbox and confirms via the API, so the path stays live and
someone is actually watching the volume. Verify at any time with:

```bash
aws sns list-subscriptions-by-topic --profile labs --region us-east-1 \
  --topic-arn "$(aws sns list-topics --profile labs --region us-east-1 \
    --query "Topics[?contains(TopicArn,'labs-jj-alerts')].TopicArn" --output text)" \
  --query 'Subscriptions[].{Endpoint:Endpoint,Arn:SubscriptionArn}' --output table
```

Any row whose `Arn` reads `PendingConfirmation` or `Deleted` is **not**
receiving alerts.

### What the alarms watch, and why these thresholds

The original three alarms all watched the **database**. On 2026-07-29 the web
tier sat at 100% CPU for 54 minutes with ALB p95 latency in the tens of seconds
— and all three stayed `OK`, because the database was healthy throughout (7–12%
CPU, sub-ms IO). The added alarms watch the tier that actually failed.

Thresholds come from the measured 5-day distribution, not from feel:

| Alarm                                | Fires when                              | Normal baseline                                                           |
| ------------------------------------ | --------------------------------------- | ------------------------------------------------------------------------- |
| `labs-jj-web-cpu-high`               | **busiest task's** CPU ≥ 90% for 15 min | median **0.8%**                                                           |
| `labs-jj-alb-latency-high`           | ALB **p95** > 10s for 15 min            | median p95 **0.23s**; only 1.7% of buckets exceed 10s                     |
| `labs-jj-alb-5xx-high`               | ALB-generated 5xx > 25 in 5 min         | ~1–5 per 15 min; incident peaked at 84                                    |
| `labs-jj-alb-target-5xx-high`        | **labs web tier** 5xx > 10 in 5 min     | 20 non-zero buckets of 1440 over 5 days, max 5; 09-08 outage peaked at 17 |
| `labs-jj-canopy-web-target-5xx-high` | **canopy-web** 5xx > 10 in 5 min        | one non-zero bucket (3) in 14 days; 09-08 outage 83–373 per bucket        |
| `labs-jj-ace-web-target-5xx-high`    | **ace-web** 5xx > 10 in 5 min           | 14-day hourly max 4                                                       |
| `labs-jj-web-no-healthy-targets`     | healthy hosts < 1 for 3 min             | every task unhealthy at once, so this is a full outage                    |
| `labs-jj-rds-connections-high`       | connections > 90 for 10 min             | 5–15; **was 120 and never fired at a 106 peak**                           |

Latency is alarmed on **p95, not Average** — Average is dragged toward zero by
health checks and static assets and stayed unremarkable through the whole
incident.

Web CPU is alarmed on **Maximum, not Average**, for the same reason applied
across tasks rather than across requests. `Average` on an ECS _service_ metric
is the mean over its tasks, so at `desiredCount=2` one task pinned at 100%
beside an idle one reads as ~50% and can never cross a 90% line. Over
2026-08-24 05:00–10:00 UTC, 17 of 60 five-minute periods had `Maximum ≥ 90%`
and none had `Average ≥ 90%` (highest Average: 58.1%) — the tier was returning
502s and this alarm stayed green. **Re-check any per-task statistic here
whenever `desiredCount` changes.**

The two 5xx alarms are a **pair, and neither substitutes for the other.**
`HTTPCode_ELB_5XX_Count` means the ALB could not get a usable response;
`HTTPCode_Target_5XX_Count` means the app answered with an error. On
2026-07-29 target 5xx was **1** while ELB 5xx peaked at **84**. On 2026-08-31
the inverse happened: **410** target 5xx over two hours (all `/umami/api/send`,
a Prisma `P2002` race) while ELB 5xx peaked at **19** — under the ELB
threshold, so nothing paged for two hours. Watching either metric alone is
blind to one of these; watch both.

The target-5xx alarm is **per target group, one per tenant**, because the ALB
fronts three application tiers (labs, canopy-web at `/canopy/*`, ace-web at
`/ace/*`) plus Umami, and a LoadBalancer-only alarm names none of them. On
2026-09-08 the labs RDS instance hit storage-full (#1634) and the ALB counted
~1,250 target 5xx in 45 minutes: **~1,200 from canopy-web**, 1–17 per bucket
from labs, 2 from Umami — and the page that fired (#1628) could not say so.
canopy-web's database is on the same instance, so a labs storage or connection
failure is always a canopy-web outage too, and canopy-web is the control plane
that dispatches the responders. The tenant alarms live in
`labs-tenant-monitoring.yml` (its own stack, `labs-jj-tenant-monitoring`) rather
than in the tenants' own templates because those deploy through
`github-actions-labs-deploy`, which has no `cloudwatch:*` grant — an alarm
resource there 403s and rolls back the deploy — and rather than in
`labs-monitoring.yml` because that file is at the inline size limit (§ Deploy).
Move them into the app stacks once the deploy role carries `PutMetricAlarm`
(canopy-web #729).

### Deploy: tenant alarms

```bash
aws cloudformation deploy \
  --region us-east-1 --profile labs \
  --stack-name labs-jj-tenant-monitoring \
  --template-file infra/labs-tenant-monitoring.yml
```

Imports `labs-jj-alert-topic-arn` from `labs-jj-monitoring`, so that stack must
exist first and cannot be deleted while this one stands. No parameters need
carrying forward — every default is the live value. Confirm both alarms exist
and are evaluating after the deploy:

```bash
aws cloudwatch describe-alarms --profile labs --region us-east-1 \
  --alarm-names labs-jj-canopy-web-target-5xx-high labs-jj-ace-web-target-5xx-high \
  --query 'MetricAlarms[].[AlarmName,StateValue,Threshold]' --output table
```

Note these are deliberately **not** paired with a timeout reduction: long-running
audit work legitimately needs the 600s gunicorn and ALB idle timeouts. The
alarms are how we learn that long requests are hurting, since the timeouts never
will.

### Deploy: ALB access logs

```bash
aws cloudformation deploy \
  --region us-east-1 --profile labs \
  --stack-name labs-jj-access-logs \
  --template-file infra/labs-access-logs.yml
```

The stack owns only the bucket. Logging is an attribute on the ALB, which this
stack does not own, so switch it on once:

```bash
LB=$(aws elbv2 describe-load-balancers --names labs-jj-alb --profile labs \
  --region us-east-1 --query 'LoadBalancers[0].LoadBalancerArn' --output text)

aws elbv2 modify-load-balancer-attributes --profile labs --region us-east-1 \
  --load-balancer-arn "$LB" \
  --attributes Key=access_logs.s3.enabled,Value=true \
               Key=access_logs.s3.bucket,Value=labs-jj-alb-access-logs \
               Key=access_logs.s3.prefix,Value=alb
```

That call **validates the bucket policy** and fails if delivery wouldn't work,
so a success is real. Confirm within a minute or two:

```bash
aws s3 ls s3://labs-jj-alb-access-logs/ --recursive --profile labs | head
# alb/AWSLogs/<account>/ELBAccessLogTestFile   <- written immediately on enable
```

Real logs then arrive on a ~5 minute cadence. Two gotchas worth knowing: ALB
delivery does **not** support SSE-KMS (the bucket is AES256 for this reason —
KMS silently drops logs), and logs are delivered on a delay, so they are a
forensic record, not a live view.

### Why there are two layers of request telemetry

During the 2026-07-29 incident the site was effectively down for 54 minutes and
there was **no way to ask "which URL is slow?"** — access logs were off and the
app emitted no request lines. The web log group held ~40,000 lines for that
window and not one recorded a request's cost; the culprit had to be found by
counting repeated outbound `httpx` INFO lines by hand.

Both layers now exist, and they answer different questions:

| Layer                     | Where                                                  | Answers                                                                                                | Blind to                            |
| ------------------------- | ------------------------------------------------------ | ------------------------------------------------------------------------------------------------------ | ----------------------------------- |
| **ALB access logs**       | S3, this stack                                         | which requests, how many, how slow, what status — for **every** request, recorded outside the process  | why a request was expensive         |
| **App request telemetry** | `connect_labs/utils/request_telemetry.py` → CloudWatch | DB queries + outbound HTTP calls per request, and to which host — for requests that breach a threshold | anything that never reaches the app |

The ALB half still works when the app is too saturated to log, is being
OOM-killed, or never received the request. The app half is the only thing that
can say _139 outbound calls to connect.dimagi.com in one request_ — which is
exactly what the incident was, and what took a day to establish without it.

**A third, metric-only layer is the one the read-only incident role can see.**
`labs-jj-observe-readonly` can read CloudWatch _metrics_ but not log events, so
neither table row above answers "which route is slow, and how much traffic is it
taking?" for it. Every web request therefore also writes one CloudWatch Embedded
Metric Format (EMF) line (`emit_route_metric` in `request_telemetry.py`), which
CloudWatch Logs turns into metrics as the awslogs driver ships it — no IAM grant,
no `PutMetricData` call, no stack to deploy:

| Namespace       | Metrics                                                 | Dimensions (exactly these)        |
| --------------- | ------------------------------------------------------- | --------------------------------- |
| `Labs/Requests` | `RequestLatency` (Milliseconds), `RequestCount` (Count) | `route`, `method`, `status_class` |

`route` is the URL pattern template (`labs/workflow/<int:workflow_id>/`), never the
raw path; anything that did not resolve (404s, scanners) is `unmatched`, and a
non-standard method is `OTHER`, so cardinality is bounded by the urlconf. EMF copies
every key of the line into the log event, so the line holds the two values and three
dimensions and nothing else — no ids, usernames or query strings; a test pins that.
Unlike the diagnostic lines it covers **every** request (it is not threshold-gated or
sampled). Kill switch: `TELEMETRY_ROUTE_METRICS=false` (pin it in
`deploy/task-definitions/web.json`). Each `route × method × status_class` seen in an
hour is a billed custom metric (×2), so keep new dimensions out.

### Deploy: audit + analytics stack

```bash
LISTENER_ARN=$(aws elbv2 describe-listeners --profile labs --region us-east-1 \
  --load-balancer-arn "$(aws elbv2 describe-load-balancers --names labs-jj-alb \
    --profile labs --region us-east-1 --query 'LoadBalancers[0].LoadBalancerArn' --output text)" \
  --query 'Listeners[?Port==`443`].ListenerArn' --output text)

aws cloudformation deploy \
  --region us-east-1 --profile labs \
  --stack-name labs-jj-audit-analytics \
  --template-file infra/labs-audit-analytics.yml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides HttpsListenerArn=$LISTENER_ARN
```

### Deploy: outbound email stack

```bash
aws cloudformation deploy \
  --region us-east-1 --profile labs \
  --stack-name labs-jj-email \
  --template-file infra/labs-email.yml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    SendingDomain=labs.connect.dimagi.com \
    EventEmail=you@dimagi.com
```

Creating the stack is **not** sufficient to send mail. SES stays unverified
until the three DKIM CNAMEs in the stack Outputs are published in the
`connect.dimagi.com` zone (which lives outside the labs account — there are no
Route 53 hosted zones here), and the account must leave the SES sandbox via an
AWS Support request. Full runbook: **[docs/OUTBOUND_EMAIL.md](../docs/OUTBOUND_EMAIL.md)**.

### Observe-only access (`labs-jj-observe-readonly`)

The everyday incident questions ("what did ALB p95 do?", "when did this alarm
fire?", "did the rollout reach steady state?") need **telemetry, not data**. Until
this stack the only path to them was the `labs` SSO profile with
AdministratorAccess, which an unattended cloud-runner turn cannot complete, and
which hands a laptop turn full admin over a HIPAA account. `labs-jj-observe-readonly`
is the read-only, non-sensitive path: metrics, alarm definitions + history, and
ALB / ECS / RDS / ElastiCache / labs-stack metadata. It **cannot** read log
events, S3 object bodies (audit archive, exports, ALB access logs), secret or
parameter values, task commands or environments (`DescribeTasks`,
`DescribeTaskDefinition`), stack parameters/outputs, or anything in the database, and
it grants no write. The template's comments say why each action is or is not
sensitive.

```bash
aws cloudformation deploy --profile labs --region us-east-1 \
  --stack-name labs-jj-observe \
  --template-file infra/labs-observe-readonly.yml \
  --capabilities CAPABILITY_NAMED_IAM
```

Trust is account-root plus an `aws:PrincipalArn` match, so **the caller's own
identity policy must also allow `sts:AssumeRole` on this role** — the stack alone
lets nobody in. Who may assume it:

| Caller                                          | How                                                                                                      | Login?                                                                                      |
| ----------------------------------------------- | -------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| A human on a laptop                             | the same `labs` SSO login, then a chained profile (below), or a `LabsObserveReadOnly` permission set     | **No new login.** Same SSO sign-in; you pick a narrower role instead of AdministratorAccess |
| An agent on a canopy cloud runner (cloud-ec2-*) | a root-owned broker on the box assumes the role with the instance role and hands the runner uid 1h creds | **None.** The instance role is the identity; runner uids never see it (IMDS is root-only)   |
| An agent on a laptop                            | canopy-web (`canopy-web-task-role`) assumes the role and vends 1h creds to the authenticated agent       | **None.** The agent's canopy identity is the gate; no phone approval                        |

A human's chained profile, needing nothing but the existing `labs` session:

```ini
[profile labs-observe]
source_profile = labs
role_arn = arn:aws:iam::858923557655:role/labs-jj-observe-readonly
region = us-east-1
```

For an Identity Center permission set instead, create it in the Identity Center
admin account with a **customer-managed policy reference** to
`labs-jj-observe-readonly` (the managed policy this stack creates), and assign it
to this account.

Verify after deploy that the denies hold (each must say `AccessDenied`):

```bash
aws cloudwatch describe-alarms --alarm-name-prefix labs-jj- --profile labs-observe --query 'length(MetricAlarms)'   # works
aws logs filter-log-events --log-group-name /ecs/labs-jj-web --limit 1 --profile labs-observe                        # AccessDenied
aws ecs describe-task-definition --task-definition labs-jj-web --profile labs-observe                                # AccessDenied
aws secretsmanager get-secret-value --secret-id labs-jj-umami-app-secret --profile labs-observe                       # AccessDenied
aws ecs describe-tasks --cluster labs-jj-cluster --tasks "$(aws ecs list-tasks --cluster labs-jj-cluster --profile labs-observe --query 'taskArns[0]' --output text)" --profile labs-observe  # AccessDenied
```

It can also read the **scrubbed** groups `/labs-jj/observe/*` (written by
`labs-observe-pipeline.yml`: ECS stop reasons, API calls, request telemetry and
ALB requests with every identifying field removed), stack status/events/templates
for `labs-jj-*`, `canopy-*` and `ace-*`, runner EC2/ASG state, RDS config, cost
and budgets, ACM, SES status and trail status. Performance Insights stays denied
until it is enabled and its `pi:Dimensions` gate is tested live.

Anything sensitive (raw application logs, the HIPAA telemetry review, raw ALB
access logs, task overrides, CloudTrail request parameters, alarm enable/disable)
stays on the `labs` SSO admin path with a person's approval.

### Deploy: observe pipeline (scrubbed derivatives)

`labs-observe-pipeline.yml` (stack `labs-jj-observe-pipeline`) writes
allow-listed copies of the sources the observe role may not read raw into
`/labs-jj/observe/*`: ECS task stops and deployments (stop code/reason, exit
codes, task def family:revision; never overrides), AWS Health, management API
calls that wrote or failed (who/what/error; never request parameters), labs
slow-request telemetry (route template, method, status, `*_ms`; never the path
or username) and every ALB request (target group, method, route template with
identifying segments as `{id}`, statuses, latencies, target, bytes; never client
IP, user agent or query). It also owns the account's management-events trail
(`labs-jj-management`, private bucket) and count-only metric filters: ERROR
lines by logger (`Labs/Web`, `Labs/Worker`, `Labs/CanopyWeb`), audit events by
action/outcome/source (`Labs/Audit EventCount`) and Umami P2002s.

The scrubbers live in `infra/observe_pipeline/` and are tested in
`connect_labs/utils/tests/test_observe_scrubbers.py`. The template's `ZipFile`
blocks are copies: edit the `.py`, then `python infra/observe_pipeline/render.py`.
It needs EventBridge notifications on the ALB log bucket (`labs-access-logs.yml`),
so deploy that stack first:

```bash
aws cloudformation deploy --profile labs --region us-east-1 \
  --stack-name labs-jj-access-logs --template-file infra/labs-access-logs.yml
aws cloudformation deploy --profile labs --region us-east-1 \
  --stack-name labs-jj-observe-pipeline --template-file infra/labs-observe-pipeline.yml \
  --capabilities CAPABILITY_NAMED_IAM
```

### Upgrading Umami

**The version is a pinned tag (`UmamiVersion`, currently `v3.3.1`), not `main`.**
It was an unpinned `git clone --depth 1` of the default branch until 2026-09-05,
which meant nothing recorded what was running. That is not a tidiness point — the
one image ever built (2026-07-24, from `b70eda56`) reported `"version": "3.2.0"`
while actually being a month of unreleased post-v3.2.0 `main`, and it missed the
upstream fix for the `/umami/api/send` P2002 race (`7c030e4c`, 2026-07-30) by
seven days. See connect-labs #1396.

An upgrade is therefore a deliberate two-part change:

1. **Bump `UmamiVersion`** to a real upstream release tag and deploy the stack.
   The build fails loudly if the tag does not resolve exactly.
2. **Build and roll:**
   ```bash
   aws codebuild start-build --project-name labs-jj-umami-build --profile labs
   aws ecs update-service --cluster labs-jj-cluster --service labs-jj-umami \
     --force-new-deployment --profile labs
   ```

⚠️ **Umami releases carry Prisma migrations, and they apply to the `umami`
database on container start** — the upgrade is a schema change, not just a new
image.

**Do NOT predict the set with `git diff` between the two tags.** That answers
"which migration FILES changed", and what actually runs is
_(migrations present in the target image)_ minus _(rows already in
`_prisma_migrations`)_ — and the database can be behind its own running image.

Ours was. On 2026-09-05 a `git diff` between the build point and `v3.3.1`
reported **three** pending migrations, the upgrade log applied **four**, and the
extra one (`21_add_session_link`, upstream 2026-07-15) had been sitting
unapplied in the previously-running image. Undercounting a forward-only schema
change is exactly the kind of confident wrong answer this runbook exists to
prevent, so read the applied set from the DATABASE, not from git:

```bash
# what the database has actually applied (source of truth)
psql "$UMAMI_DATABASE_URL" -c \
  'SELECT migration_name, finished_at FROM _prisma_migrations ORDER BY migration_name;'
# what the target image carries
git ls-tree -r --name-only <target-tag> -- prisma/migrations/ | grep migration.sql
```

The difference between those two lists is what will run. Read every one of them;
`23_update_session_data` in the v3.3.1 set **deletes rows** (duplicate
`session_data`, keeping the newest per `session_id,data_key`) before adding its
unique index.

The upgrade log is the after-the-fact record — it names each migration as it
applies, so keep it:

```bash
aws logs get-log-events --log-group-name /ecs/labs-jj-umami \
  --log-stream-name "$(aws logs describe-log-streams --log-group-name /ecs/labs-jj-umami \
      --profile labs --order-by LastEventTime --descending \
      --query 'logStreams[0].logStreamName' --output text)" \
  --profile labs --start-from-head --limit 60 --query 'events[].message' --output text
```

**Rollback:** set `UmamiVersion` to an earlier release tag and deploy. The task
image is **derived** from `UmamiVersion` (there is no separate image parameter —
one that could be set independently is how the task definition briefly
advertised v3.3.1 while running a 2026-07-24 image, #1439), so a rollback moves
the image and its label together.

The schema migrations are **not** reversed by that — Prisma migrations are
forward-only. Verify a migration is acceptable before it applies, not after; the
restore path for a bad one is RDS point-in-time recovery on `labs-jj-postgres`
(7-day retention), which restores the **whole instance**, not just the `umami`
database.

The pre-pin 2026-07-24 image is kept addressable as `:pre-pin-2026-07-24`
because the first pinned build overwrote the `:latest` that was its only tag. It
has no release tag and is deliberately **not** reachable via `UmamiVersion`;
deploying it is an out-of-band `register-task-definition`, not a stack parameter.

**Which version is running:** `UMAMI_BUILD_VERSION` on the task definition —

```bash
aws ecs describe-task-definition --task-definition labs-jj-umami --profile labs \
  --query 'taskDefinition.containerDefinitions[0].environment'
```

Do not read Umami's own `package.json` for this; that is the value that lied.

## Referenced resources created out-of-band (2026-07-24)

These are stateful and deliberately kept out of CloudFormation so stack
operations can never touch them. Recorded here for reproducibility:

- **`s3://labs-jj-audit-archive`** — audit-event archive
  (`docs/AUDIT_LOGGING.md`). Created with `--object-lock-enabled-for-bucket`,
  default retention **COMPLIANCE mode, 6 years** (objects are undeletable by
  anyone until their retention lapses — that is the point), Block Public
  Access on, lifecycle `audit-events/` → Glacier at 90d → Deep Archive at
  365d.
- **Secrets Manager** — `labs-jj-umami-database-url` (postgres URL for the
  `umami` DB on labs-jj-postgres; carries
  `?sslmode=no-verify&sslaccept=accept_invalid_certs` because node-pg and
  Prisma parse SSL opts differently and RDS presents the RDS CA) and
  `labs-jj-umami-app-secret` (random hex for Umami session signing).
- **ECR `labs-jj-umami`** — holds the custom-built image (BASE_PATH baked).
- **`umami` database + role on labs-jj-postgres** — created via ECS exec
  (`CREATE ROLE umami LOGIN PASSWORD ...; CREATE DATABASE umami OWNER umami`).
- **CloudWatch retention on `/ecs/labs-jj-web` + `/ecs/labs-jj-worker`** — set
  to **731 days** via `aws logs put-retention-policy` (groups are owned by the
  ECS deploy layer, not CF; they previously had NO retention policy). The
  6-year audit copy lives in the S3 archive, not CloudWatch.

- **Web ALB target group `labs-jj-target-group`** — health check `/health/`
  every **10s**, healthy after **2**, unhealthy after **3**, timeout 5s;
  `deregistration_delay.timeout_seconds` = **60**. Set 2026-09-10 via
  `aws elbv2 modify-target-group` / `modify-target-group-attributes` (was
  30s / 2 / 3 and 300s). Why: `/health/` is a static JSON view, and the two
  30s checks were ~60s of every rollout's ~4-minute steady-state wait. The
  300s drain was not on that path (ECS counts a task out of the deployment
  the moment its drain starts) but held two replaced tasks alive for five
  minutes after every deploy; 60s is plenty now that gunicorn actually
  receives SIGTERM (#1697) and finishes in-flight requests itself.
  canopy-web's group on the same ALB runs 5s / 2 / 2.
  **Changed again 2026-10-14** (same two commands, by hand -- this group is not
  in any CloudFormation stack) to health check every **5s**, timeout **4s**
  (must be below the interval), healthy **2**, unhealthy **3**, and
  `deregistration_delay.timeout_seconds` = **15**. Why: the drain IS on the
  rollout's path after all. In the service events of the two rollouts on
  2026-10-14, "deployment completed" landed 60s and 62s after the last "begun
  draining connections" -- the delay, to the second. A 5s health interval
  takes ~10s off each of the two task batches becoming healthy.
  **15s, not canopy-web's 5s, on purpose:** labs has a long request tail that
  canopy-web does not. TargetResponseTime over the 7 days to 2026-10-14 was
  p99 10.2s, p99.5 15.5s, p99.8 36s (p99.9 191s is the SSE streams, cut at any
  delay). The ALB closes whatever is still in flight on a draining target when
  the delay expires, and in-flight requests skew long, so at 5s every deploy
  would 502 a slow page load or two; 15s keeps ~99.5% of requests whole and
  still takes 45 of the 60 seconds back. Revisit if the tail shrinks.

- **Application Auto Scaling on `labs-jj-web`** — min **2**, max **6** tasks,
  registered 2026-09-11 via `aws application-autoscaling
register-scalable-target` plus two target-tracking policies:
  `labs-jj-web-cpu60` (`ECSServiceAverageCPUUtilization`, target 60%) and
  `labs-jj-web-mem70` (`ECSServiceAverageMemoryUtilization`, target 70%).
  Why: the tier ran at a fixed `desiredCount=2` (1024 CPU / 4096 MB, three
  uvicorn workers each), so a burst of expensive requests — 30 MB snapshot
  streams, another team's ~110s audit calls — queued behind the six workers
  and surfaced to users as an SSE stream that received no events for minutes.
  Nothing was wrong with the data; there was simply nowhere to run the work.
  **Measured behaviour, read back from the live policies on 2026-09-11** (the
  targets alone do not tell you the timing, and the timing is what you need at
  3am): scale **out** fires when CPU > 60% (or memory > 70%) for **3 x 1 min**,
  with a **60s** cooldown; scale **in** fires only when CPU < **54%** (or memory
  < **63%**) for **15 x 1 min**, with a **300s** cooldown. Scale-in tracks a
  _lower_ band than the target, so coming down from 6 to 2 takes roughly **30
  minutes** of sustained quiet and no lull shorter than 15 minutes removes
  anything -- deliberate, because labs traffic is bursty and flapping would be
  worse than an idle task. The weak side is scale-OUT: it needs 3 minutes of
  sustained load plus ~1-2 minutes for a task to pass health checks, so a
  90-second spike is over before capacity arrives. The lever for that is a higher
  MINIMUM, not a tuned policy.

  **What is and is not logged.** `aws application-autoscaling
describe-scaling-activities --service-namespace ecs --resource-id
service/labs-jj-cluster/labs-jj-web` is the per-action record (cause, start and
  end time) and the right read for "did it scale, how often, for how long".
  CloudTrail's 90-day event history corroborates it through the scaler's
  `UpdateService` calls -- note there is **no CloudTrail trail** in this account,
  only the always-on event history. Two real gaps: **Container Insights is
  disabled** on `labs-jj-cluster`, so no `DesiredTaskCount`/`RunningTaskCount`
  metric exists and task count cannot be charted over time (enabling it is
  cluster-wide -- canopy-web, ace-web and umami share the cluster -- and costs per
  metric); and a target-tracking alarm's only action is its own scaling policy, so
  **nothing notifies** when the tier scales, there being no EventBridge rules at
  all. Worth knowing while reading the alarm table above: `labs-jj-alerts` has
  exactly **one** subscriber (`hal@dimagi-ai.com`), so every alarm in this stack
  reaches a single mailbox.

  **Two consequences worth knowing.** Scaling policies move `desiredCount`,
  which is exactly what the per-task-statistic note above says to re-check —
  `labs-jj-web-cpu-high` is alarmed on `Maximum` for that reason and stays
  correct as the count moves, but any statistic added there must be re-read
  against a variable task count, not against 2. And target tracking scales on
  the service AVERAGE, so a single pinned task beside idle ones will not
  trigger it; it answers sustained tier-wide load, not one slow endpoint.

## Future slices (not yet implemented)

- **RDS `idle_session_timeout` backstop.** A server-side reaper so any future
  idle-connection leak self-heals. Requires a custom DB parameter group +
  associating it to the instance (a brief reboot), or an `ALTER ROLE ... SET
idle_session_timeout` on the app role (no reboot). Deferred — the PR #765 fix
  already eliminated the known leak; this is belt-and-suspenders.
- Importing the core RDS / ECS / ALB resources under CloudFormation.
