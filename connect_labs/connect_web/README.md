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
| `PoolWorker` (phone, name, device id; encrypted backup code, PIN, PersonalID password and tokens)           | `models.py`                                              |
| `WorkerSession` (who has which worker checked out); `FormSession` (form sessions a checkout opened)         | `models.py`                                              |
| PersonalID client: `start_configuration`, `complete_recovery`, `password_grant`                             | `personalid.py`                                          |
| Connect mobile API client: list, `start_learn_app`, learn/delivery progress, claim, confirm payments        | `connect_api.py`                                         |
| HQ: server from an app's `install_url`, the worker's HQ username, the HQ token                              | `hq.py`                                                  |
| Shared HTTP: pooled clients, time budgets, structured errors, retries on GETs only                          | `http.py`, `errors.py`                                   |
| **The service layer** every page (and the form engine) goes through                                         | `service.py`                                             |
| What labs reuses of the worker's Connect/HQ data, for how long, and what drops it                           | `cache.py`                                               |
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
service.progress(session, opportunity_id, kind) -> dict  # one kind, fresh (the post-submit poll)
service.start_learning(session, opportunity_id) -> None
service.claim(session, opportunity_id) -> bool       # guarded: learning must be complete
service.confirm_payment(session, payment_id, confirmed: bool, *, opportunity_id=None) -> None
service.confirm_payments(session, [(payment_id, confirmed), ...], *, opportunity_id=None) -> None
service.form_submitted(session, opportunity_uuid, kind) -> None  # drops what a submit changes
service.hq_token(session, domain, hq_base=None) -> str
service.form_engine_context(session, opportunity, app_kind, *, with_token=True) -> dict
#   -> {hq_base, domain, app_id, install_url, hq_username, hq_token}; app_kind "learn" | "deliver"
```

`opportunity_id` is Connect's `opportunity_id` (uuid) or its numeric `id`; the service always
looks the opportunity up in the worker's own list first and sends Connect's uuid, never the URL's.

## The external call sequence

**Sign-in** (`service.ensure_signed_in`). The PersonalID password, access token and HQ tokens are the
_worker's_ (the phone's), so they live on `PoolWorker` and survive a release: a new checkout signs in
with the saved password, never with a recovery unless PersonalID refuses that password. Cheapest
first:

1. A cached PersonalID access token with more than 60 seconds left.
2. `POST {PERSONALID_SERVER_URL}/o/token/` (form) `grant_type=password, client_id=CONNECT_WEB_PERSONALID_CLIENT_ID, scope=openid, username, password` with the password saved on the worker.
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

Only one request signs a worker in at a time — a short cache lock (`cw:v1:signin:<worker>`, 60 s,
others wait up to 30 s, then get a retryable "taking longer than usual") — and **no database
transaction is open across a network call**: what a sign-in learns (a rotated password, the
username, a pause) is written in short updates as it goes. If the cache is down, sign-in falls back
to the worker's row lock held across the calls (slower, still never two recoveries).

A Connect call answered 401 drops the cached token (unless another request already replaced it),
signs in again and retries once.

**Connect** (`CONNECT_PRODUCTION_URL`, `Authorization: Bearer <PersonalID token>`,
`Accept: application/json;version=2.0`):

- `GET /api/opportunity/` — the worker's opportunities.
- `POST /users/start_learn_app/` (form `opportunity=<uuid>`) — creates the HQ user in the learn
  domain. A bare 500 (no invite) is shown as "the worker has no invite to this opportunity".
- `GET /api/opportunity/<uuid>/learn_progress`, `GET /api/opportunity/<uuid>/delivery_progress`.
- `POST /api/opportunity/<uuid>/claim` (`{}`) — `OPPORTUNITY_FULL` / `OPPORTUNITY_ENDED` shown
  in plain language. **Connect does not check that learning is complete; `service.claim` does**:
  every learn module completed and an assessment passed, as the app requires.
- `FAILED_USER_CREATE` (400/502) on start_learn_app or claim means HQ refused to create the
  worker's mobile user in that app's project space (learn domain, or deliver domain for a claim).
  Shown as not retryable, naming the domain and the two usual causes on HQ: the project space lacks
  the `COMMCARE_CONNECT` feature flag, or Connect's API key for the opportunity belongs to an HQ web
  user who can't edit mobile workers there (`errors.HQUserNotCreated`).
- `POST /api/payment/confirm` `{"payments": [{"id": <payment uuid or id>, "confirmed": "true"|"false"}]}` — `confirmed` is a string.

**CommCare HQ** (the server is the host of the app's `install_url`, which must be https and on
`CONNECT_WEB_HQ_HOSTS`; with no install_url, `CONNECT_WEB_DEFAULT_HQ_URL`):

- `POST <hq>/oauth/token/` (form) `grant_type=password, client_id=CONNECT_WEB_HQ_CLIENT_ID, scope="mobile_access sync", username=<personalid username, lowercased>@<domain>.commcarehq.org, password=<PersonalID access token>` → about 15 minutes. Cached per (HQ server, domain) on the worker; a
  refreshed token is merged in under the worker's row lock after a re-read (the HTTP call is made
  outside the lock), so two domains refreshed at once both keep their tokens.

**HTTP** (`http.py`): one pooled `httpx.Client` per process (keep-alive, 50 connections / 20 kept
alive) and a separate one for Formplayer, so a page's several calls share TLS connections. Every
operation has a **total** budget (20 s; the connect timeout is 3 s; each attempt's timeout is cut to
what is left, and a retry starts only if there is time for it). Only GETs are retried (connection
errors, 502/503/504, and read timeouts except on Connect's list, which may legitimately take ~10 s
and gets one 18 s read); a POST is sent once.

### What labs caches (`cache.py`)

Namespaced per **pool worker** (the data is the worker's) with a generation counter in every key, so
`bump` drops everything at once. Redis is shared and unencrypted, so **tokens are never cached there**
(they stay in Postgres, encrypted), nor POST results, Formplayer responses or form state, nor errors
(except a 30-second "no released build"). A cache outage degrades to direct fetches, never to an
error.

| Entry                              | TTL    | Dropped by                                                              |
| ---------------------------------- | ------ | ----------------------------------------------------------------------- |
| `opps` (the worker's list)         | 60 s   | start learning, claim, confirm payment, a submitted form                |
| `learn:<opp>` / `deliv:<opp>`      | 30 s   | start learning (learn); claim (both); confirm payment (deliv); a submit |
| `target:<opp>:<kind>` (app target) | 10 min | claim; an engine error or no released build                             |
| `build:<host>:<domain>:<app>`      | 5 min  | an engine error. Shared by all workers; "no released build" kept 30 s   |
| `awaiting:<opp>`                   | 5 min  | set by a submitted form: the opportunity page reads Connect, not cache  |
| `synced:<domain>`                  | 1 day  | when labs last asked Formplayer to `sync-db` the worker (see below)     |

Within a request, values are memoised on the `WorkerSession` object, so nothing is fetched twice; a
15-second single-flight lock keeps the player and the progress poll from both fetching the list. A
miss in a cached list is checked once against a fresh list (a new invite shows at once). The claim
guard reads learn progress fresh. Learn and delivery progress are read concurrently.

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
  `EncryptedTextField`s on `PoolWorker` (Fernet, `LABS_FIELD_ENCRYPTION_KEYS`). None appears in a
  repr, a str, an admin form, a page, a log line or the cache; errors carry only an HTTP status and
  an `error_code`.
- **The browser never sees a PersonalID or HQ token.** Pages render data labs fetched; the form
  player talks only to labs' API, which adds the HQ token, HQ username, domain and build id itself.
- Releasing a session ends it and forgets its form sessions; the worker's password and tokens stay
  on the worker for the next checkout (they are the worker's, like a phone's). Saving a new backup
  code in the admin resumes a paused sign-in.
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

- `POST <CONNECT_WEB_FORMPLAYER_URL>/<endpoint>`, JSON. `X-Labs-Signature` (v1) = padded base64
  HMAC-SHA256(`CONNECT_WEB_FORMPLAYER_KEY`, the exact body bytes sent); for `answer_media`, of the
  multipart's `application/json` part (`answer`), the file part unsigned. `X-Labs-HQ-Token` = the
  worker's HQ token (`service.hq_token`), fresh each request.
- **Signature v2**, sent alongside v1: `X-Labs-Timestamp` (unix seconds) and `X-Labs-Signature-V2` =
  base64 HMAC-SHA256(key, `"v2\n" + "POST" + "\n" + "/<endpoint>" + "\n" + ts + "\n" +
sha256hex(hq token) + "\n" + <the X-Labs-HQ-Host value, or ""> + "\n" + <the signed bytes>`) — so a
  captured request can't be replayed later, aimed at another endpoint, or reused with another token
  or HQ server. Formplayer verifies v2 when present (±300 s) and falls back to v1 alone only while
  configured to; labs sends both, so either side can deploy first. `formplayer.sign_v2`; the test
  vector is pinned in `tests/test_foundation.py`.
- Time budgets (one attempt each): navigate and `sync-db` 75 s (they may restore the worker and
  install the app), `submit-all` 60 s, everything else 30 s. The player gives up at 80 s / 35 s with
  a retryable error, so a hung request never blocks its queue.
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

| Route (`/connect-web/api/opportunities/<opp>/…`)                                          | Formplayer endpoint                                            |
| ----------------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| `<kind>/navigate/` `{selections[], offset?, search_text?, cases_per_page?}`               | `navigate_menu_start` (no selections) / `navigate_menu`        |
| `<kind>/details/` `{selections[]}` (ending in the case id)                                | `get_details` (response key `details`)                         |
| `<kind>/answer/` `{session_id, ix, answer}`                                               | `answer`                                                       |
| `<kind>/answer-media/` multipart `session_id, ix, file` (JPEG/PNG by magic bytes, ≤ 4 MB) | `answer_media`                                                 |
| `<kind>/new-repeat/`, `<kind>/delete-repeat/` `{session_id, ix}`                          | `new-repeat`, `delete-repeat`                                  |
| `<kind>/change-locale/` `{session_id, locale}`                                            | `change_locale`                                                |
| `<kind>/submit/` `{session_id, answers{ix: answer}, meta_location?}`                      | `submit-all` (`prevalidated: true`)                            |
| `<kind>/sync/`                                                                            | `sync-db`                                                      |
| `progress/?kind=learn\|deliver` (GET)                                                     | — (Connect progress of that kind, fresh: the post-submit poll) |

Scoping: the opportunity must be in the worker's own list; domain, HQ username, build id and token
come from `formplayer.target_for` (cached 10 minutes in the worker's namespace; the form page fills
it, so the first navigation makes no Connect call). A form session id is accepted only if a
navigation in the same checkout opened it for the same opportunity and kind — a `FormSession` row,
not the Django session (which every request re-saves whole, so a second tab could write back a copy
without the first tab's form). A request whose `Content-Length` is over the limit (2 MB JSON; the
photo limit plus 64 KB for an upload) is refused with 413 before its body is read — the API views
run the CSRF check themselves, after that check, because the middleware reads a multipart body.
Player calls also keep the checkout's last-used time fresh (at most one write a minute). Inputs are
validated (`formplayer.clean_*`): form index syntax,
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

### Restore freshness

Formplayer keeps each worker's restored case data between requests and re-reads HQ only when asked,
so cases created elsewhere (a phone, another tool, a case another worker changed) would stay stale
until a new app build. Labs asks: the player's **first navigation after a page load** carries
`start: true`, and if labs hasn't synced the worker in that domain for 10 minutes — or doesn't know
(a cold cache counts as stale) — it calls Formplayer `sync-db` first. Best effort: a failed sync is
logged and the player carries on with what Formplayer has. A successful sync (also the manual
`<kind>/sync/`) is recorded in the cache (`synced:<domain>`).

Not verified against a real Formplayer yet (none is deployed): the request/response shapes come from
its source and are pinned in `tests/fixtures/formplayer_responses.json`.
