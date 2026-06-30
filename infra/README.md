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

| Template              | Owns                                                                         | References (does not own)    |
| --------------------- | ---------------------------------------------------------------------------- | ---------------------------- |
| `labs-monitoring.yml` | SNS alert topic, RDS-connection + slot-exhaustion alarms, log metric filters | RDS instance, ECS log groups |

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

## Future slices (not yet implemented)

- **RDS `idle_session_timeout` backstop.** A server-side reaper so any future
  idle-connection leak self-heals. Requires a custom DB parameter group +
  associating it to the instance (a brief reboot), or an `ALTER ROLE ... SET
idle_session_timeout` on the app role (no reboot). Deferred — the PR #765 fix
  already eliminated the known leak; this is belt-and-suspenders.
- Importing the core RDS / ECS / ALB resources under CloudFormation.
