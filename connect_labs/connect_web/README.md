# Connect Web

A mobile-first web version of the CommCare Connect worker app, at `/connect-web/`. A labs user
checks out a worker from labs' **pool of Connect test workers** and does what the Android app
does — see opportunities, start learning, claim, see delivery progress and payments, confirm
payments — with real data landing in the Connect opportunity.

**Status:** the foundation. Identity, the PersonalID / Connect / HQ clients, the service layer,
the models and the first mobile pages are here. **Filling in forms is not**: Learn and Deliver
forms will run in a Formplayer-based form engine (a separate track). Until then "Continue in
form" and "New visit" open a placeholder (`connect_web:form_entry`). The engine itself — patched
formplayer, its build and its AWS service — is [`formplayer/`](../../formplayer/README.md); `manage.py
connect_web_formplayer_check` checks labs can reach it with the shared key.

## Architecture

Labs' backend acts as the worker's **phone**. The browser only ever talks to labs; labs holds
the worker's PersonalID password and tokens server-side and makes every PersonalID, Connect and
HQ call itself.

| Piece                                                                                                       | File                                 |
| ----------------------------------------------------------------------------------------------------------- | ------------------------------------ |
| `PoolWorker` (phone, name, encrypted backup code and PIN, PersonalID username, stable device id)            | `models.py`                          |
| `WorkerSession` (who has which worker checked out; encrypted password, cached PersonalID and HQ tokens)     | `models.py`                          |
| PersonalID client: `start_configuration`, `complete_recovery`, `password_grant`                             | `personalid.py`                      |
| Connect mobile API client: list, `start_learn_app`, learn/delivery progress, claim, confirm payments        | `connect_api.py`                     |
| HQ: server from an app's `install_url`, the worker's HQ username, the HQ token                              | `hq.py`                              |
| Shared HTTP: timeouts, structured errors, retries on GETs only                                              | `http.py`, `errors.py`               |
| **The service layer** every page (and the form engine) goes through                                         | `service.py`                         |
| Who may use it                                                                                              | `access.py`                          |
| Pages: worker picker, My Opportunities, opportunity (About / Learn / Claim / Deliver / Payments), form stub | `views.py`, `templates/connect_web/` |
| Admin for pool workers; `connect_web_add_worker`                                                            | `admin.py`, `management/commands/`   |
| Fake PersonalID / Connect / HQ for tests (one `httpx.MockTransport`)                                        | `tests/fakes.py`                     |

Labs' record of each Connect Worker is `workers.ConnectWorker`, keyed on the PersonalID username;
a pool worker learns that username at its first sign-in (`PoolWorker.personalid_username`) and
`PoolWorker.connect_worker` finds the matching record. PersonalID's base URL is the
`PERSONALID_SERVER_URL` Labs Messaging already uses.

### The service API (the form engine's seam)

```python
service.check_out(user, pool_worker, *, take_over=False) -> WorkerSession
service.release(session) -> None
service.current_session(user) -> WorkerSession | None
service.ensure_signed_in(session) -> str            # PersonalID access token (cached)
service.list_opportunities(session) -> list[dict]   # {"opp": <Connect item>, stage, progress...}
service.opportunity_detail(session, opportunity_id) -> dict
service.start_learning(session, opportunity_id) -> None
service.claim(session, opportunity_id) -> bool       # guarded: learning must be complete
service.confirm_payment(session, payment_id, confirmed: bool) -> None
service.confirm_payments(session, [(payment_id, confirmed), ...]) -> None
service.hq_token(session, domain, hq_base=None) -> str
service.form_engine_context(session, opportunity, app_kind, *, with_token=True) -> dict
#   -> {hq_base, domain, app_id, install_url, hq_username, hq_token}; app_kind "learn" | "deliver"
```

`opportunity_id` is Connect's `opportunity_id` (uuid) or its numeric `id`; the service always
looks the opportunity up in the worker's own list first and sends Connect's uuid, never the URL's.

## The external call sequence

**Sign-in** (`service.ensure_signed_in`), cheapest first:

1. A cached PersonalID access token with more than 60 seconds left.
2. `POST {PERSONALID_SERVER_URL}/o/token/` (form) `grant_type=password, client_id=CONNECT_WEB_PERSONALID_CLIENT_ID, scope=openid, username, password` with the password saved on the session.
3. Only if that fails with `invalid_grant` (or there is no password yet), full recovery — the
   Android app's flow for an existing account:
   1. `POST /users/start_configuration` (JSON, `Accept: application/json;version=3.0`, no auth)
      `{phone_number, application_id: "org.commcare.dalvik", cc_device_id: <PoolWorker.device_id>, device: "Connect Web (labs)"}` → `{token, required_lock, demo_user, ...}`. No Play
      Integrity headers: connect-id skips that check for numbers ever invited to an opportunity,
      and pool numbers are `+7426` demo numbers, validated without an OTP.
   2. `POST /users/recover/complete_recovery` with `Authorization: Bearer <token>`
      (connect-id's `SessionTokenAuthentication`) and `{method: "backup_code", backup_code}` →
      `{username, password, db_key, invited_user, ...}`. **A wrong code is HTTP 200 with
      `{attempts_left}`** — treated as failure and never retried: the pool worker's sign-in is
      paused (`sign_in_blocked_reason`) until an admin saves a new code, because connect-id locks
      the account after a few wrong codes. Every recovery rotates the worker's password (accepted:
      a phone signed in as the same worker will have to sign in again).
   3. The password grant (step 2) with the new password.

A Connect call answered 401 drops the cached token, signs in again and retries once.

**Connect** (`CONNECT_PRODUCTION_URL`, `Authorization: Bearer <PersonalID token>`,
`Accept: application/json;version=2.0`):

- `GET /api/opportunity/` — the worker's opportunities.
- `POST /users/start_learn_app/` (form `opportunity=<uuid>`) — creates the HQ user in the learn
  domain. A bare 500 (no invite) is shown as "the worker has no invite to this opportunity".
- `GET /api/opportunity/<uuid>/learn_progress`, `GET /api/opportunity/<uuid>/delivery_progress`.
- `POST /api/opportunity/<uuid>/claim` (`{}`) — `OPPORTUNITY_FULL` / `OPPORTUNITY_ENDED` shown
  in plain language. **Connect does not check that learning is complete; `service.claim` does**:
  every learn module completed and an assessment passed, as the app requires.
- `POST /api/payment/confirm` `{"payments": [{"id": <payment uuid or id>, "confirmed": "true"|"false"}]}` — `confirmed` is a string.

**CommCare HQ** (the server is the host of the app's `install_url`, which must be https and on
`CONNECT_WEB_HQ_HOSTS`; with no install_url, `CONNECT_WEB_DEFAULT_HQ_URL`):

- `POST <hq>/oauth/token/` (form) `grant_type=password, client_id=CONNECT_WEB_HQ_CLIENT_ID, scope="mobile_access sync", username=<personalid username, lowercased>@<domain>.commcarehq.org, password=<PersonalID access token>` → about 15 minutes. Cached per (HQ server, domain).

Only GETs are retried (network errors, timeouts, 502/503/504); a POST is sent once.

## Settings

| Setting                             | Default                                           | Notes                                                         |
| ----------------------------------- | ------------------------------------------------- | ------------------------------------------------------------- |
| `CONNECT_WEB_PERSONALID_CLIENT_ID`  | empty                                             | The PersonalID public client the Android app uses. Required   |
| `CONNECT_WEB_HQ_CLIENT_ID`          | empty                                             | The HQ OAuth client for mobile tokens. Required for HQ        |
| `CONNECT_WEB_FORMPLAYER_URL`        | empty                                             | The labs formplayer (`formplayer/`); empty = no form engine   |
| `CONNECT_WEB_FORMPLAYER_KEY`        | empty                                             | Signs every formplayer request (formplayer's `LABS_AUTH_KEY`) |
| `CONNECT_WEB_DEFAULT_HQ_URL`        | `https://www.commcarehq.org`                      | HQ for an app with no `install_url`                           |
| `CONNECT_WEB_HQ_HOSTS`              | `www.`, `india.`, `eu.`, `staging.commcarehq.org` | The only hosts a worker's token is sent to                    |
| `CONNECT_WEB_ALLOWED_EMAILS`        | empty                                             | Non-staff labs sign-ins that may use Connect Web              |
| `CONNECT_WEB_IDLE_TAKEOVER_MINUTES` | `60`                                              | When someone else's checked-out worker may be taken over      |
| `PERSONALID_SERVER_URL`             | `https://connectid.dimagi.com`                    | Shared with Labs Messaging                                    |
| `CONNECT_PRODUCTION_URL`            | `https://connect.dimagi.com`                      | Shared with the rest of labs                                  |
| `LABS_FIELD_ENCRYPTION_KEYS`        | —                                                 | Encrypts the pool's secrets and every token at rest           |

An empty client id fails loudly (`NotConfigured`) before anything is sent — in particular before
a recovery that would rotate the worker's password.

## Access

Dimagi staff (labs' `is_dimagi_staff`: Django `is_staff` or a Dimagi sign-in), plus the emails
in `CONNECT_WEB_ALLOWED_EMAILS`. Labs has no feature-flag library (waffle was removed in the labs
simplification), so the opt-in is that env list. Everyone else gets a 403; anonymous visitors
go to the labs login.

A labs user has at most one worker checked out, and a worker at most one labs user (both are
database constraints). Checking out a second worker releases the first; someone else's worker
can be taken over only after `CONNECT_WEB_IDLE_TAKEOVER_MINUTES` of inactivity.

## Adding a pool worker

The worker must already exist in PersonalID with a backup code, on a `+7426` demo number, and
have been invited to at least one opportunity (so connect-id skips the integrity check).

```bash
# Secrets from the environment...
CONNECT_WEB_BACKUP_CODE=... CONNECT_WEB_PIN=... \
  python manage.py connect_web_add_worker --phone +74260000001 --name "Test Worker 1"
# ...or a hidden prompt (no env), or stdin: line 1 backup code, line 2 PIN
python manage.py connect_web_add_worker --phone +74260000001 --name "Test Worker 1" --stdin < secrets.txt
```

Secrets are never taken on the command line. Running it again for the same phone updates the
worker; a new backup code also resumes a sign-in paused by a rejected code. Django admin
(Connect Web › Pool workers) does the same; its backup-code and PIN fields are write-only.

## Security notes

- The pool's backup codes and PINs, the PersonalID password and every token are
  `EncryptedTextField`s (Fernet, `LABS_FIELD_ENCRYPTION_KEYS`). None appears in a repr, a str, an
  admin form, a page or a log line; errors carry only an HTTP status and an `error_code`.
- **The browser never sees a PersonalID or HQ token.** Pages render data labs fetched; the form
  engine will receive `form_engine_context` server-side.
- Releasing a session wipes its password and tokens.
- A worker's PersonalID token is only ever sent to PersonalID, to `CONNECT_PRODUCTION_URL` and to
  an allow-listed https HQ host.
- Tests run entirely against `tests/fakes.py`; nothing in the suite can reach a real server.
