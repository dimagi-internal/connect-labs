# Connect Web

A mobile-first web version of the CommCare Connect worker app, at `/connect-web/`. A labs user
checks out a worker from labs' **pool of Connect test workers** and does what the Android app
does — see opportunities, start learning, claim, see delivery progress and payments, confirm
payments — with real data landing in the Connect opportunity.

**Status:** the foundation plus **the form player**: Learn and Deliver forms run in the browser on
Formplayer (commcare-core's engine, in its labs mode), and land in HQ → Connect as if from the
phone. See [The form player](#the-form-player). The engine itself — patched formplayer, its build
and its AWS service — is [`formplayer/`](../../formplayer/README.md); `manage.py
connect_web_formplayer_check` checks labs can reach it with the shared key.

## Architecture

Labs' backend acts as the worker's **phone**. The browser only ever talks to labs; labs holds
the worker's PersonalID password and tokens server-side and makes every PersonalID, Connect and
HQ call itself.

| Piece                                                                                                       | File                                                     |
| ----------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| `PoolWorker` (phone, name, encrypted backup code and PIN, PersonalID username, stable device id)            | `models.py`                                              |
| `WorkerSession` (who has which worker checked out; encrypted password, cached PersonalID and HQ tokens)     | `models.py`                                              |
| PersonalID client: `start_configuration`, `complete_recovery`, `password_grant`                             | `personalid.py`                                          |
| Connect mobile API client: list, `start_learn_app`, learn/delivery progress, claim, confirm payments        | `connect_api.py`                                         |
| HQ: server from an app's `install_url`, the worker's HQ username, the HQ token                              | `hq.py`                                                  |
| Shared HTTP: timeouts, structured errors, retries on GETs only                                              | `http.py`, `errors.py`                                   |
| **The service layer** every page (and the form engine) goes through                                         | `service.py`                                             |
| Who may use it                                                                                              | `access.py`                                              |
| Pages: worker picker, My Opportunities, opportunity (About / Learn / Claim / Deliver / Payments), form page | `views.py`, `templates/connect_web/`                     |
| The Formplayer bridge: signing client, build-id resolver, input validation, one function per endpoint       | `formplayer.py`                                          |
| The form player's JSON API (a proxy to Formplayer, scoped on the server)                                    | `api.py`                                                 |
| The form player (React): menus, case list, case detail, form entry, submit                                  | `static/connect_web/`, entry `static/js/connect-web.tsx` |
| Admin for pool workers; `connect_web_add_worker`                                                            | `admin.py`, `management/commands/`                       |
| Fake PersonalID / Connect / HQ for tests (one `httpx.MockTransport`)                                        | `tests/fakes.py`                                         |

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
| `CONNECT_WEB_MEDIA_MAX_BYTES`       | 4 MB − 1 KB                                       | Largest photo; never above Formplayer's own limit             |

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
  player talks only to labs' API, which adds the HQ token, HQ username, domain and build id itself.
- Releasing a session wipes its password and tokens.
- A worker's PersonalID token is only ever sent to PersonalID, to `CONNECT_PRODUCTION_URL` and to
  an allow-listed https HQ host.
- Tests run entirely against `tests/fakes.py`; nothing in the suite can reach a real server.

## The form player

`connect_web:form_entry` (`/connect-web/opportunities/<opp>/form/<learn|deliver>/`) runs the
opportunity's learn or deliver app. "Start learning" (Connect's `start_learn_app`, which creates the
worker's HQ user) goes straight on to it; "Continue learning" and "New visit" open it directly.
Deliver needs a claim (the service refuses otherwise).

```
browser (React, static/connect_web)  --JSON, CSRF-->  labs api.py  --signed-->  Formplayer (labs mode)  -->  CommCare HQ  ~~async~~>  Connect
```

### The engine contract (Formplayer `docs/labs-mode.md`, `auth/LabsAuthFilter.java`)

- `POST <CONNECT_WEB_FORMPLAYER_URL>/<endpoint>`, JSON. `X-Labs-Signature` = padded base64
  HMAC-SHA256(`CONNECT_WEB_FORMPLAYER_KEY`, the exact body bytes sent); for `answer_media`, of the
  multipart's `application/json` part (`answer`), the file part unsigned. `X-Labs-HQ-Token` = the
  worker's HQ token (`service.hq_token`), fresh each request.
- Every body has `username` (the HQ username) and `domain`, set by `formplayer.Formplayer` after
  anything else, so a caller can't change them; `restoreAs` is always dropped.
- **`app_id` is the released build id**, not Connect's `cc_app_id`: `resolve_build_id` reads
  `GET <hq>/a/<domain>/apps/download/<cc_app_id>/media_profile.ccpr?latest=true` (no auth) and takes
  the id from the `PostURL` property (`/receiver/secure/<build_id>/`); cached 5 minutes per (HQ,
  domain, app). No release → "no released build" (`NO_RELEASED_BUILD`).
- Formplayer reports most failures as HTTP 200 `{"status": "error", "exception": ...}`: a lost form
  session ("Could not find form with id") becomes `SESSION_EXPIRED` (410, "start the form again");
  any other is shown as "The form engine reported a problem: <its text, cut short>". 401 is a key
  mismatch (`ENGINE_AUTH_FAILED`), 423 a per-user lock (`ENGINE_BUSY`, retryable). A `submit-all`
  HQ refused is a normal result (`status: "error"`, `notification.message`), shown with "nothing
  was saved, try again".

### The API (`api.py`; all JSON; POST + CSRF except progress)

| Route (`/connect-web/api/opportunities/<opp>/…`)                                          | Formplayer endpoint                                           |
| ----------------------------------------------------------------------------------------- | ------------------------------------------------------------- |
| `<kind>/navigate/` `{selections[], offset?, search_text?, cases_per_page?}`               | `navigate_menu_start` (no selections) / `navigate_menu`       |
| `<kind>/details/` `{selections[]}` (ending in the case id)                                | `get_details` (response key `details`)                        |
| `<kind>/answer/` `{session_id, ix, answer}`                                               | `answer`                                                      |
| `<kind>/answer-media/` multipart `session_id, ix, file` (JPEG/PNG by magic bytes, ≤ 4 MB) | `answer_media`                                                |
| `<kind>/new-repeat/`, `<kind>/delete-repeat/` `{session_id, ix}`                          | `new-repeat`, `delete-repeat`                                 |
| `<kind>/change-locale/` `{session_id, locale}`                                            | `change_locale`                                               |
| `<kind>/submit/` `{session_id, answers{ix: answer}, meta_location?}`                      | `submit-all` (`prevalidated: true`)                           |
| `<kind>/sync/`                                                                            | `sync-db`                                                     |
| `progress/` (GET)                                                                         | — (Connect learn/delivery progress, for the post-submit poll) |

Scoping: the opportunity must be in the worker's own list; domain, HQ username, build id and token
come from `formplayer.target_for` (cached 2 minutes per session/opportunity/kind). A form session id
is accepted only if a navigation in _this_ labs session opened it for the same worker, opportunity and
kind (kept in the Django session). Inputs are validated (`formplayer.clean_*`): form index syntax,
menu path items, answer shapes and sizes, `meta_location` ranges, time zone. Errors are
`{"error": {code, message, retryable}}` with 400 / 403 / 409 / 410 / 502 / 503.

### The player (`static/connect_web`)

- **Layout: one screen per question, as on the phone.** A `field-list` group is one screen (the app
  author's way of saying "these together"); a plain group's questions are screens of their own,
  titled with the group's caption; a repeat (its instances and "add another") is one screen. Sticky
  Back / Next; Next refuses to pass an unanswered required question; a final "ready to submit"
  screen. "Show all questions" switches to one scrolling page (remembered per device).
- Questions (`tree.widgetFor`): text, phone/`numeric`, int, long, decimal, date, time, datetime,
  select (big radio rows), `minimal` select (dropdown), `combobox` (and any select over 30 choices:
  type to search), multi-select, labels with markdown (escaped, then a few tags), groups, field-lists,
  repeats (add / remove), geopoint (`navigator.geolocation`), photo (`capture=environment`, shrunk
  in the browser to fit Formplayer's 4 MB), barcode as text. **Signature, audio, video and document
  upload say "can't be captured on the web yet"**; if one is required the form says it can't be
  submitted here. Case search (`type: "query"`) and multi-select case lists aren't supported yet.
- Irrelevant questions are simply absent from Formplayer's tree, so the tree is shown as sent and
  replaced after every answer; the player keeps its place by form index. Text is saved 0.7 s after
  typing stops (and on blur); everything else on change; requests go one at a time.
- Required and constraint errors show inline; submit-all validation errors jump to the first.
- **Submit** asks the browser for its location and sends it as `meta_location` ("lat lon alt
  accuracy"). Denied on a deliver form → a warning that the visit may be flagged "GPS missing", with
  "Try again" / "Submit without location"; a learn form just submits. Success shows HQ's message,
  then returns to the opportunity page with `?submitted=<kind>`, which says "Submitted — waiting for
  Connect to process" and polls `progress/` (3 s … 60 s, about three minutes) against the numbers the
  player saw before submitting, reloading once Connect shows the form.
- States: loading skeletons, retry on network errors, engine not configured (server-rendered and
  from the API), session expired ("Start the form again"), offline banner, leave-form confirm.

Not verified against a real Formplayer yet (none is deployed): the request/response shapes come from
its source and are pinned in `tests/fixtures/formplayer_responses.json`.
