# PersonalID authorization for the field surface

Status: plan, written 2026-10-29. Nothing below the "Interim" section is built.
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

## 3. Interim (built): the link knows who it was sent to

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

### Stage 1 — one-time code through the PersonalID chat (labs + OCS; no app change)

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
- Build: a `verification` model (hashed code, attempts, expiry, username), a
  send step, a verify step, a session cookie + device-key binding, and the
  "sent to" labels become "verified" only after this.

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

## 5. The field surface itself (separate from authorization)

Design notes for the page behind the link, for the slice after this one:

- **Plain HTML plus small vanilla JS**, ES5-friendly for old WebViews, no
  framework, no third-party hosts (drop Google Fonts, self-host a system font
  stack), first load budget ~100–150 KB gzip. Measure the real
  `vendors.css` / `tailwind.css` weight — unmeasured.
- **Offline:** a service worker caches the shell and the link's reference data
  (its supply points, items, units). Writes go to an **IndexedDB outbox** with
  a client-generated UUID per record, used as `source.ref` — the operations
  already replay a repeated ref+payload as one write, so retries are safe.
  Sync on the `online` event, on page open, and with a visible "Send N
  pending" button; do not rely on Background Sync (Chrome-only, flaky).
- **Time:** record the device's timestamp and let the server record received
  time; cheap phones have bad clocks and entries arrive days late.
- **Photos:** `<input type="file" accept="image/*" capture="environment">`
  (most reliable on low-RAM phones), downscale client-side to ~1280 px JPEG q≈0.7
  (~100–200 KB), upload separately from the data record so a flaky connection
  never blocks the numbers.
- **QR:** `BarcodeDetector` is inconsistent on old devices; use a small JS
  decoder on a downscaled frame / still, with "photograph it and decode" and
  type-the-code fallbacks. Print the code in readable form beside the QR.
- **First open** still needs enough signal to load one small page; add
  "add to home screen" so later opens are cached.
- **Two sources:** the same stock fact may come from this page or a CommCare
  form (`source=commcare_form`, `form_submission_id`). Dedupe on those and the
  client UUID; show provenance in the UI; define precedence when two sources
  disagree for the same day.
- **Test** on a throttled 2G profile and a real Android Go phone — emulators
  misreport RAM.

## 6. Open questions / things to verify

1. Is a Markdown link in a Connect message actually tappable on a current app
   build, and does a bare URL really not link? (Read from code, not run.)
2. Push delivery latency for a code on a poor network on an old phone.
3. Which OCS chatbot and team labs should send through, and who owns it; how a
   user who has not consented is onboarded.
4. Whether the Android and connect-id owners will take Stage 2 or 3, and on what
   timeline.
5. Whether person-links should default to a shorter expiry than 30 days.
6. Whether `sent to` attribution needs to appear anywhere partners see it
   (today: the page, the links list, submissions, audit).
