# PersonalID authorization for the field surface

Status: written 2026-10-29. §3 (interim attribution), §4 Stage 1 (code through the PersonalID chat) and the
core of §5 (the offline field page) are **built**; Stages 2 and 3 are plans that need Android / connect-id work.
Read from source at each repo's `main` on that date: `dimagi/connect-id`,
`dimagi/commcare-android`, `dimagi/open-chat-studio`, `dimagi/commcare-connect`
and this repo. **Nothing here was run on a device**; where a claim rests on
reading code rather than observing behaviour it says so.

## 1. The problem

The supply chain needs a lightweight surface a field user opens on a phone to
scan QR codes, photograph goods, and enter stock. It is reached from a link we
send them through Connect messaging. We want, eventually, **real
authorization that the person using it is the PersonalID user we sent it to** —
not "whoever holds the link".

Constraints that shape everything: users are on old, low-end Android phones, on
slow or absent networks, and the same stock facts may also arrive from a
CommCare app running in the Connect opportunity (so the ledger already has to
accept several sources for one fact).

## 2. How PersonalID, messaging and the app work today

### 2.1 PersonalID (`connect-id`) is an OIDC provider, but only for the app

- django-oauth-toolkit with `OIDC_ENABLED`, mounted at `/o/`
  (`connectid/urls.py`, `connectid/settings.py` `OAUTH2_PROVIDER`). Scopes:
  `openid`, `sync`. `PKCE_REQUIRED` is `False`.
- ID-token / userinfo claims (`users/oauth.py`, `ConnectOAuth2Validator`):
  `sub` = the PersonalID **username**, plus `name`, `phone` (E.164),
  `is_active`.
- The user's credential is a **device-bound password** held by the Connect
  app. `validate_user` calls `check_login_from_different_device`; a login from
  a different device is refused (`LOGIN_FROM_DIFFERENT_DEVICE`). Phone OTP via
  Firebase exists only for registration and recovery sessions
  (`users/views.py`: `validate_firebase_id_token`, `confirm_otp`, recovery).
- Google Play Integrity is checked at registration (`utils/app_integrity/`).
- **There is no browser sign-in.** No login or consent templates exist beyond
  the admin and an app-install redirect page; `users/views.login` is a stub
  (`pass`). The only non-test OAuth application types in the repo use the
  password / client-credentials grants.
- Server-to-server callers are `MessageServer` rows with their own
  credentials (`messaging/models.py`).

### 2.2 Messaging is a chat between a server and the app

- `Channel` = (connect user, message server), with `user_consent`. `Message`
  `content` / `rich_text` are **encrypted with a per-channel key**; PersonalID
  cannot read them (`messaging/rich_messages.py` docstring). Attachments are
  encrypted files.
- Delivery: FCM push, then the app polls `messaging/retrieve_messages/` and
  fetches the channel key. The user can reply.

### 2.3 Labs reaches PersonalID users through Open Chat Studio

This is the path that already works (workflow `start_ocs_outreach`):

1. `OCSDataAccess.trigger_bot(identifier=<PersonalID username>,
   platform="commcare_connect", experiment_id=<bot>, message_text=<text>)`
   (`connect_labs/labs/integrations/ocs/api_client.py`). `message_text` is sent
   **verbatim** — no LLM involved (`prompt_text` is the LLM path).
2. OCS (`apps/api/trigger_bot.py`) calls `ensure_commcare_connect_ready`:
   creates the PersonalID channel if there is none and **requires the user's
   consent**; otherwise it refuses with OCS's own reason.
3. `CommCareConnectSender` (`apps/channels/connect_channel.py`) encrypts with a
   key OCS generates and sends via connect-id `create_message`. OCS already
   sends links as text (a file too big to attach goes as name + download URL).

Consequences: the sender must be a configured OCS chatbot on the
`commcare_connect` channel; the user must have consented in the app; OCS can
read what it sends; delivery depends on push on the user's phone.

#### How the OCS channel is wired (the part that is easy to forget)

Four things line up, and a send only works when all four do:

| In OCS | What it is | Where it is set |
|---|---|---|
| **Chatbot** (`Experiment`) | The bot; its public id is what labs passes as `experiment_id`. | OCS, per team. |
| **Channel** (`ExperimentChannel`, platform `commcare_connect`) | "This chatbot may talk on PersonalID." Carries `commcare_connect_bot_name`, the name the user sees in the app. **One per chatbot.** If a chatbot has no such channel, a send is refused ("Create the channel first"). | OCS → the chatbot → Channels. |
| **Participant** (`Participant`, identifier = the PersonalID username) | The person, per team. | Created on first send. |
| **Participant data** (`ParticipantData`, per participant *and* chatbot) | `system_metadata.commcare_connect_channel_id` (the PersonalID channel), `consent`, and the **encryption key**. | Created on first send. |

On the first send OCS calls connect-id `create_channel` (`channel_source` = the OCS channel's stable
`external_id`, display name = the bot name) and stores the returned `channel_id` and `consent`. Consent
then arrives *from connect-id*: when the user accepts messages from that bot in the app, connect-id
calls OCS `/api/commcare_connect/consent` (HMAC-signed). Until then OCS refuses with "User has not given
consent". The app fetches the channel's key from OCS (`/api/commcare_connect/generate_key`, authenticating
with its PersonalID token, which OCS checks against connect-id to learn the username), and decrypts with it.
Replies come the other way: connect-id → OCS `/channels/commcare_connect/incoming_message`.

So, for labs to message someone: pick a chatbot that **has a commcare_connect channel**; labs must be
connected to OCS **as a person in that chatbot's team**; and the user must have **opened that bot's chat
in the app and accepted**. The first message to a brand-new person therefore cannot be delivered until they
have consented — see §6.

### 2.4 What the app does with message text and links (`commcare-android`)

- `ConnectMessageAdapter.bind` → `MarkupUtil.setMarkdown` → Markwon, built in
  `CommCareApplication.getMarkwonInstance()` with only the **table**,
  **strikethrough** and a **link-resolver** plugin. No Linkify plugin, so
  *(from reading, unverified on device)* **a bare URL is not tappable; a
  Markdown link `[text](url)` is.** `update_links/messaging.py` builds that.
- A tapped link falls through to `LinkResolverDef` → the system default
  browser. There is no in-app WebView for Connect content.
- `minSdk 23` (Android 6). Service workers, IndexedDB, `capture=` file inputs
  and `getUserMedia` are all available on every supported phone's Chrome.
- The only link type that opens *inside* the app is a verified App Link:
  `connect.dimagi.com` and `connect-staging.dimagi.com` under
  `/users/invite_redirect/` (`AndroidManifest.xml`, `DeepLinkHelper.kt`).
- The app **already mints a PersonalID access token** whenever it needs one:
  `ApiPersonalId.retrievePersonalIdToken` posts `grant_type=password`,
  `scope=openid`, the app's public `client_id`, and the stored username and
  password. It then exchanges that token for HQ SSO tokens
  (`ConnectSsoHelper.retrieveHqSsoToken`). "App proves the user to another
  service" is an established pattern.

### 2.5 How other services trust PersonalID tokens

`commcare-connect` validates PersonalID tokens as a resource server by calling
`/o/introspect/` with client credentials
(`RESOURCE_SERVER_INTROSPECTION_URL` in its settings). Labs has no such client
today; its OAuth login is against Connect (`export` scope), not PersonalID.

### 2.6 The labs page that exists

`/supply/u/<token>/` (`connect_labs/supply_chain/update_links/`): server-rendered
Django forms, CSRF on every form, **no script at all**, no analytics, no cache,
no indexing, `Referrer-Policy: same-origin`. The token is 32 random bytes; only
an HMAC of it is stored. Writes go through the ordinary operations as
`supplier_reported` / `partner_reported`, attributed to the link's
organisation. It loads the shared `vendors.css` / `tailwind.css` and **Google
Fonts** (a third-party request on every load — to remove for poor networks).
There is no service worker or manifest anywhere in the repo.

Stock is modelled per person already: a field worker is a `SupplyPoint` of kind
`user_held` carrying `connect_username` (rutf-procurement design §18), and
`StockCount` has `connect_username`, `visit_id`, `form_submission_id`, with
sources including `commcare_form` and `connect_visit`.

## 3. Built: the link knows who it was sent to, can be sent, and can ask for proof

Until authorization exists, the link is deliberately **unauthenticated: anyone
holding it can use it**. What is added is attribution:

- `UpdateLink.connect_username` — the PersonalID user the link was sent to
  (optional; empty = sent to an organisation's desk). Set by
  `update_link_issue` (`connect_username`), the issue form, and the MCP tool.
- A **listed** link sent to a person also covers that person's own
  `user_held` supply point(s) in the programme, so a worker can count their
  own stock.
- `UpdateLinkSubmission.connect_username` snapshots the recipient at write
  time (it survives revoke / re-issue); the audit event carries it; a stock
  count's own `connect_username` is set.
- The page and the links list say "sent to <username>".
- `update_links/messaging.link_message()` produces the message text with a
  tappable Markdown link.
- **Sending it** (`update_link_issue` → `send_via_bot`): the link goes to the user's Connect chat
  through the chosen OCS chatbot, as the person issuing it (their stored OCS connection, as a
  workflow's coaching send does). The link exists either way; a refusal is reported beside it.
  `SUPPLY_LINK_OCS_BOT` preselects a chatbot. Synthetic programmes send nothing.
- **Proof** (`require_verification`, Stage 1 below): optional per link.

**Known limitation.** The count form offers only products the link can
already name: its orders' items and anything that has ever moved at its
supply points. A worker sent a link with no order and no stock movements
therefore has nothing to count. Normal flow (stock is distributed to the
worker first) is fine; an issuer-selectable product scope is the fix if the
empty-bag case matters.

**What this does and does not claim.** It is "the link we sent to X was used",
not "X used it". Everything surfaced must say *sent to*, never *verified*. A
forwarded link, a shared phone, or a leaked URL all attribute work to X.
Mitigations that exist: 256-bit token, 90-day maximum expiry, revocation, a
per-link write budget, scope limited to the link's own rows. Mitigations to add
cheaply before wider use: a shorter default expiry for person-links; first-open
binding of the link to the browser (below) so a forwarded link at least stops
working elsewhere.

## 4. The plan: real authorization, staged

Three ways to prove "this is PersonalID user X", cheapest first.

### Stage 1 — one-time code through the PersonalID chat (labs + OCS; no app change) — **BUILT**

The page asks for the username (or already knows it from the link) and has
labs send a 6-digit code into the user's Connect chat via the §2.3 path. The
user reads it in the app and types it into the browser; labs then binds a
**device key** (WebCrypto non-extractable key in IndexedDB) to the session.

- Proves: possession of the registered device and the app (the message only
  reaches them there). Same shape as SMS OTP, over the E2E channel.
- Needs: an OCS bot on the `commcare_connect` channel for labs; the user's
  consent; rate limits per username and per IP; code expiry generous enough
  for push delay (10 min) with resend.
- Weakness: relies on push delivery on poor networks; OCS can read the code
  (fine for a code, not for anything sensitive).
- Built (`update_links/verification.py`): `LinkVerification` (hashed code, attempts, expiry),
  `LinkDevice` (hashed device secret in a cookie), the verify page, and `verified` on each
  submission. Ten-minute codes, five tries, one use, 30 s between sends, five an hour.
  Codes go out as the link's issuer, so their OCS connection has to stay alive.
- Not done: a *device key* (WebCrypto) instead of a cookie secret — the cookie is HttpOnly and
  hash-stored, which is enough for now; per-IP limits on code requests.

### Stage 2 — token handoff from the app (app + connect-id + labs)

A verified App Link in the message opens the **app**; the app mints its
PersonalID token (§2.4) and opens a Custom Tab to labs with a one-time code (or
the token). Labs verifies against connect-id's introspection endpoint and gets
a real `sub` and `phone`.

- Proves: the device credential, strongest and smoothest (no typing).
- Needs: (a) a new App Link host/path in `AndroidManifest.xml` and handler;
  (b) the app calls an exchange endpoint rather than putting a long-lived
  bearer token in a URL; (c) a labs client registered in connect-id with
  introspection rights, as `commcare-connect` has; (d) connect-id hosting
  `assetlinks.json` for the host (it already serves one at
  `.well-known/assetlinks.json`).
- Security notes: never put an access token in a URL; use a short-lived,
  single-use code exchanged server-side; bind to the browser's device key.

### Stage 3 — "Log in with PersonalID" in the browser (connect-id + app)

A standard authorization-code flow with approval on the phone (push or QR
scan): the browser shows a code, the app approves with the device credential,
connect-id issues the auth code. Cleanest, and reusable by every Connect
product, but it is a new feature in connect-id and the app. Treat as the
destination; Stages 1–2 are the road.

### Recommendation

Build Stage 1 now (labs-only), keep Stage 2 as the upgrade to propose to the
Android and connect-id owners, and treat Stage 3 as the long-term target. In
all stages: authenticate once, online; then hold a device-bound session so
offline queues sync without a fresh PersonalID proof.

## 5. The field surface (built)

`/supply/u/<token>/field/` — `update_links/field.py`, `templates/supply_chain/field.html`,
`static/supply_chain/field/field.js`. Linked from the form page ("Count stock on your phone") and shown
on the issued-link screen. It does one job, a **stock count**, and is built for old phones on poor networks:

- **Light.** One inline-CSS page and one ES2015 script, no framework, no build step, **no request to any
  other site** (the form page still loads Google Fonts and the labs bundles; this does not). About 32 KB raw
  for page + script + worker (~9 KB gzipped); pinned by a test (<40 KB raw).
- **Offline.** A service worker keeps the page, script and the link's reference data; with a poor
  connection it serves the kept copy after 4 s while refreshing in the background. The QR decoder is
  fetched after the worker is running, not at install.
- **Saved first, sent later.** Entries go to IndexedDB, each with an id the phone makes up; sending runs on
  load, on the `online` event, every minute while open, and from a "Send now" button, with backoff. The
  server records an id once (`UpdateLinkSubmission.client_id`, unique per link), so a lost reply and a
  retry never double an entry. The phone's own clock is kept (`device_time`) beside the receipt time.
- **Photos.** `<input capture>` (works on any phone), shrunk on the phone to ≤1280 px JPEG q0.7, sent
  *after* the count in a separate request so a slow upload never holds the numbers back; type is checked by
  its first bytes, ≤4 MB, held against the stock count as a `photo` document.
- **Scanning.** Built-in `BarcodeDetector` where the browser has one; otherwise the vendored jsQR (Apache-2.0,
  130 KB, lazy). Reads GS1 Digital Link, `(01)…(10)…` element strings and bare GTINs, matches the product by
  GTIN and fills in the batch. Falls back to "photograph the code" and to typing it.
- **Same rules as the form page.** Same token, same scope, same verification gate (an unverified browser is
  sent to the form page's proof step), CSRF on, per-link write budget (higher, for a catch-up backlog).

Not built: recording receipts / dispatches / releases from the field page (the form page still does those);
language packs; a screen for correcting a rejected entry (it can only be dismissed); push to tell a
supervisor an entry arrived; the device-key session. **Not run on a real low-end phone** — see §6.

Browser tests live in `connect_labs/supply_chain/tests/e2e/test_field_page.py` (offline reopen and held entry,
lost reply, revoked link, photo shrink, photographed code, live fake camera, code parsing). They need
`pip install playwright qrcode && playwright install chromium`, skip where there is no browser, and are
`--ignore`d in `pyproject.toml` like the other e2e directories. Run: `pytest connect_labs/supply_chain/tests/e2e
-o addopts="--ds=config.settings.test --reuse-db"`.

## 6. Open questions / things to verify

1. Is a Markdown link in a Connect message actually tappable on a current app
   build, and does a bare URL really not link? (Read from code, not run.) Nothing about delivery
   — link or code — has been seen on a phone.
2. Push delivery latency for a code on a poor network on an old phone.
3. Which OCS chatbot and team labs should send through, and who owns it; how a
   user who has not consented is onboarded (the first message to someone who has never opened that
   bot's chat cannot be delivered — they have to accept in the app first, so a supply bot needs an
   onboarding path, e.g. the opportunity inviting its workers to the bot).
   Whose OCS connection sends: today the link's issuer's. A shared service connection would survive a
   person leaving; labs has no such identity yet.
4. Whether the Android and connect-id owners will take Stage 2 or 3, and on what
   timeline.
5. Whether person-links should default to a shorter expiry than 30 days.
6. Whether `sent to` attribution needs to appear anywhere partners see it
   (today: the page, the links list, submissions, audit).
7. The field page on a real low-end Android (Go edition, Android 6–8, Chrome 70-era): service-worker
   install, IndexedDB under storage pressure, canvas memory on a 12 MP photo, and the live scan frame rate
   have only been run in desktop headless Chromium.
