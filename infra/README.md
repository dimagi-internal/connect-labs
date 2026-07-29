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

| Template                   | Owns                                                                                                                                                                      | References (does not own)                                                     |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| `labs-monitoring.yml`      | SNS alert topic + subscriptions, RDS-connection + slot-exhaustion alarms, web-CPU / ALB-latency / ALB-5xx / no-healthy-target alarms, log metric filters                  | RDS instance, ECS cluster + service, ALB + target group, ECS log groups       |
| `labs-audit-analytics.yml` | Umami service (log group, target group, `/umami/*` ALB rule, task def, ECS service), Umami CodeBuild image pipeline + its role, audit-archive/secrets IAM inline policies | Object-Locked audit S3 bucket, Umami secrets, ECR repo, ALB/cluster/roles/VPC |

## Deploy

```bash
aws cloudformation deploy \
  --region us-east-1 --profile labs \
  --stack-name labs-jj-monitoring \
  --template-file infra/labs-monitoring.yml \
  --parameter-overrides AlarmEmail=you@dimagi.com
```

- Omit `AlarmEmail` (or pass empty) to create the alarms + SNS topic without an
  email subscription — alarms still fire to the topic; wire Slack/another
  endpoint to the exported `labs-jj-alert-topic-arn` later.
- After the first deploy with an email, **confirm the subscription** via the
  email AWS sends, or alarms won't reach your inbox.
- Re-run the same command to apply template changes (idempotent).

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

| Alarm                            | Fires when                      | Normal baseline                                       |
| -------------------------------- | ------------------------------- | ----------------------------------------------------- |
| `labs-jj-web-cpu-high`           | web CPU ≥ 90% for 15 min        | median **0.8%**                                       |
| `labs-jj-alb-latency-high`       | ALB **p95** > 10s for 15 min    | median p95 **0.23s**; only 1.7% of buckets exceed 10s |
| `labs-jj-alb-5xx-high`           | ALB-generated 5xx > 25 in 5 min | ~1–5 per 15 min; incident peaked at 84                |
| `labs-jj-web-no-healthy-targets` | healthy hosts < 1 for 3 min     | `desiredCount=1`, so this is a full outage            |
| `labs-jj-rds-connections-high`   | connections > 90 for 10 min     | 5–15; **was 120 and never fired at a 106 peak**       |

Latency is alarmed on **p95, not Average** — Average is dragged toward zero by
health checks and static assets and stayed unremarkable through the whole
incident.

Note these are deliberately **not** paired with a timeout reduction: long-running
audit work legitimately needs the 600s gunicorn and ALB idle timeouts. The
alarms are how we learn that long requests are hurting, since the timeouts never
will.

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

To upgrade Umami: `aws codebuild start-build --project-name labs-jj-umami-build`
(rebuilds `latest` from upstream with `BASE_PATH=/umami` baked in), then
`aws ecs update-service --cluster labs-jj-cluster --service labs-jj-umami
--force-new-deployment`.

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

## Future slices (not yet implemented)

- **RDS `idle_session_timeout` backstop.** A server-side reaper so any future
  idle-connection leak self-heals. Requires a custom DB parameter group +
  associating it to the instance (a brief reboot), or an `ALTER ROLE ... SET
idle_session_timeout` on the app role (no reboot). Deferred — the PR #765 fix
  already eliminated the known leak; this is belt-and-suspenders.
- Importing the core RDS / ECS / ALB resources under CloudFormation.
