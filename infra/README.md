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
| `labs-monitoring.yml`      | SNS alert topic, RDS-connection + slot-exhaustion alarms, log metric filters                                                                                              | RDS instance, ECS log groups                                                  |
| `labs-audit-analytics.yml` | Umami service (log group, target group, `/umami/*` ALB rule, task def, ECS service), Umami CodeBuild image pipeline + its role, audit-archive/secrets IAM inline policies | Object-Locked audit S3 bucket, Umami secrets, ECR repo, ALB/cluster/roles/VPC |
| `labs-email.yml`           | SES domain identity + DKIM, `labs-jj-email` configuration set, `labs-jj-email-events` SNS topic + event destination, scoped `ses:SendEmail` managed policy                | ECS task role (policy attaches by name), the DNS zone, SES production access  |

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
