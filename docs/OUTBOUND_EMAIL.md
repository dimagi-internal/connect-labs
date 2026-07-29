# Outbound email from labs (SES)

**Status: wired, not yet delivering.** The application side is complete and
merged; two steps that need a human are outstanding (DNS, and SES production
access). This is the runbook for both. Background: issue #1039.

## Where it stands

| Piece                                    | State                                                       |
| ---------------------------------------- | ----------------------------------------------------------- |
| Django settings pointed at SES           | ✅ `config/settings/labs_aws.py`, behind `LABS_EMAIL_ENABLED` |
| Fail-loud backend while disabled         | ✅ `connect_labs.utils.email.NotConfiguredEmailBackend`       |
| Celery-only send path                    | ✅ `connect_labs.utils.email.send_labs_email`                 |
| SES identity, config set, bounce SNS, IAM | ✅ `infra/labs-email.yml` (deploy it)                         |
| DKIM CNAMEs published in DNS             | ⛔ **needs the connect.dimagi.com zone owner**                |
| SES production access (leave the sandbox) | ⛔ **needs an AWS Support request**                           |

Until the last two land, `LABS_EMAIL_ENABLED` stays `False` and every
`send_labs_email()` logs a WARNING and delivers nothing — on purpose.

## Why it fails loudly instead of quietly

Before this work, `EMAIL_BACKEND` was Django's console backend. Every
`send_mail()` wrote to stdout and returned `1` — "one message sent." A feature
built on that would pass review, pass tests, and log success while delivering
nothing to anybody.

So the disabled state now uses `NotConfiguredEmailBackend`, which logs a WARNING
naming the subject and recipients and returns **0**. If you are reading a
CloudWatch log and see `Email NOT sent (labs outbound email is not configured)`,
that is this, working as designed.

## Turning it on

### (1) Deploy the AWS stack

Creates the SES domain identity + DKIM, the `labs-jj-email` configuration set,
the `labs-jj-email-events` SNS topic wired to bounce/complaint events, and the
scoped `ses:SendEmail` policy on `labs-jj-ecs-task-role`.

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

Confirm the SNS subscription from the email AWS sends, or bounce notifications
go nowhere.

**On the choice of domain:** `labs.connect.dimagi.com` — the host labs already
serves from. It is a labs-scoped name, never the apex and never a domain prod
Connect sends from, which matters because labs is a prototyping environment
where an experiment can plausibly send a burst of mail to a stale address list;
its sending reputation must not be able to drag down deliverability for anything
else Dimagi sends. Verifying it also covers any future subdomain of it.

The tradeoff is the CNAME constraint described in step (2): because this name
already carries a CNAME to the ALB, no TXT record can sit on it. That rules out
an SPF record at the sending domain (harmless — see below) and is the one reason
you might instead pick a dedicated mail subdomain with no CNAME of its own.

### (2) Publish the DNS records — the long pole

The `connect.dimagi.com` zone is **not** in the labs AWS account (there are no
Route 53 hosted zones there), so this stack cannot publish its own verification
records. Read them out of the stack and hand them to whoever owns the zone:

```bash
aws cloudformation describe-stacks --profile labs --region us-east-1 \
  --stack-name labs-jj-email \
  --query 'Stacks[0].Outputs[?starts_with(OutputKey,`Dkim`)||OutputKey==`SpfRecord`||OutputKey==`DmarcRecord`].[OutputKey,OutputValue]' \
  --output table
```

Three DKIM CNAMEs are **required** — SES stays unverified and refuses every send
until all three resolve. DNS for `dimagi.com` is on **Cloudflare**; the DKIM
records must be set to **DNS-only (grey cloud)**, not proxied.

> **`labs.connect.dimagi.com` is itself a CNAME** (to `labs-jj-alb-…elb.amazonaws.com`),
> and a CNAME must be the only record at its name (RFC 1034), which Cloudflare
> enforces. So **the SPF TXT record cannot be published at that exact name** —
> skip it. Nothing breaks: SES verification uses only the three DKIM CNAMEs,
> and with SES's default MAIL FROM the envelope domain is `amazonses.com`, whose
> SPF Amazon publishes, so DMARC passes on DKIM alignment.
>
> The DKIM records (`<token>._domainkey.labs.connect.dimagi.com`) and the DMARC
> record (`_dmarc.labs.connect.dimagi.com`) are *different names*, so the CNAME
> does not affect them. If SPF alignment is ever genuinely needed, the fix is a
> custom MAIL FROM on a fresh subdomain (e.g. `bounce.labs.connect.dimagi.com`),
> which carries its own MX + TXT and sidesteps the constraint.

Verify once they have propagated:

```bash
aws sesv2 get-email-identity --profile labs --region us-east-1 \
  --email-identity labs.connect.dimagi.com \
  --query '{Verified:VerifiedForSendingStatus,Dkim:DkimAttributes.Status}'
```

### (3) Request SES production access

The account is in the **sandbox** (`ProductionAccessEnabled: false`,
200 msg/day, 1 msg/sec), which means you may only send **to** addresses that are
themselves verified SES identities — useless for anything user-facing.

Request production access in the SES console (Account dashboard → *Request
production access*), or:

```bash
aws sesv2 put-account-details --profile labs --region us-east-1 \
  --production-access-enabled \
  --mail-type TRANSACTIONAL \
  --website-url https://labs.connect.dimagi.com \
  --use-case-description "..."
```

AWS will ask how you handle bounces and complaints. The answer is step (1):
a configuration set publishes every bounce/complaint to the
`labs-jj-email-events` SNS topic, and account-level suppression is enabled for
both, so a hard-bounced address is never retried. Turnaround is typically ~24h.

While you are waiting, you can still test end to end by verifying your own
address as an SES identity and sending to it.

### (4) Flip the flag and deploy

In `deploy/task-definitions/{web,worker}.json` set:

```json
{ "name": "LABS_EMAIL_ENABLED", "value": "True" }
```

then deploy from `main`:

```bash
gh workflow run deploy-labs.yml --repo dimagi-internal/connect-labs \
  --ref main --field run_migrations=false
```

If the sender and the verified domain have drifted apart, the deploy **fails its
Django system checks** (`connect_labs.E001`) rather than starting up and
discovering it against a real recipient.

### (5) Verify

```bash
aws ecs execute-command --profile labs --region us-east-1 \
  --cluster labs-jj-cluster --task <task-id> --container web --interactive \
  --command "python manage.py send_test_email --to you@dimagi.com --sync"
```

The command prints the resolved configuration, runs the same preflight as the
system check, and refuses to send if anything is off (`--force` overrides).
`--sync` sends inline so SES errors surface in the session; without it the
message is queued to Celery, which is how application code sends.

Note: **worker tasks serve stale code for 2–4 minutes after a deploy**, so give
the Celery path a moment before concluding a queued send is broken.

## Sending mail from a feature

One front door:

```python
from connect_labs.utils.email import send_labs_email

send_labs_email(
    subject="Your audit is ready",
    message=text_body,
    recipient_list=[user.email],
    html_message=html_body,   # optional
)
```

It queues `connect_labs.utils.tasks.send_mail_async` and returns immediately.
**Do not call `django.core.mail.send_mail` from a view.** `EMAIL_TIMEOUT` is 5s
and the web tier is a single 1 vCPU task with 3 gthread workers — a slow SES
call on the request path is a latency incident waiting to happen (#1037).

Transient failures (SES throttles labs at 1 msg/sec even with production access)
retry three times with exponential backoff, then fail the task loudly.

## Operational notes

- **Bounces and complaints** land on the `labs-jj-email-events` SNS topic.
  Watch the complaint rate; SES will suspend sending if it climbs.
- **Suppression** is on at the account level for `BOUNCE` and `COMPLAINT`, and
  restated on the configuration set. A hard-bounced address is never retried.
- **The `labs-jj-alerts` SNS topic is unrelated.** It delivers email through SNS's
  own infrastructure and needed none of this setup — a fact that made labs look
  more email-capable than it was.
- **Turning it back off** is one env var. The identity, DNS records and IAM
  policy can all stay in place.
