# Embedding labs in other systems

Status: approved for build (Jonathan, 2026-10-23: "this sounds good ... lets write it up
and then implement it"). Phase 1 is built in `connect_labs/embed/`. Phases 2 and 3 are
designed here so phase 1 does not paint them into a corner.

## The problem

Connect prod wants to show labs workflows inside its own pages, in an iframe, without the
person signing in to labs a second time. Other systems will want the same, and Connect will
want to frame other people's tools too. Labs refuses all framing today
(`X_FRAME_OPTIONS = "DENY"`, `frame-ancestors 'none'`). A bare "allow framing" would expose
every labs page to clickjacking, and would still not work: a third-party iframe gets no labs
session, because browsers no longer send ordinary cookies across sites.

## What others do

**Tableau Connected Apps** (what CommCare HQ's newer integration uses for its REST API, and
what Tableau recommends for embedding). There are two layers:

1. _System trust._ A site admin registers the host application: a client id, a secret (HS256,
   "direct trust") or the host's JWKS ("OAuth 2.0 trust"), the domains allowed to embed, and the
   projects it may reach.
2. _User identity._ The host signs a short JWT naming the viewer (`sub`, plus `scp`,
   `aud`, `jti`, `exp` ≤ 10 min). Tableau applies **that user's** permissions.

What the viewer sees is (what the app may reach) ∩ (what the user may see).

**CommCare HQ** (`corehq/apps/reports/standard/tableau.py`, `models.py`) embeds with the older
_trusted tickets_ mechanism:

- **System trust:** HQ's server IP is on Tableau's trusted-host list.
- **The ticket:** HQ POSTs `username=HQ/<username>` (or `HQ/<role name>` in role mode) and
  gets a one-time ticket, which the iframe redeems.
- **HQ's own check:** HQ checks `can_view_tableau_viz` first.
- **Mirrored users:** HQ copies its users and groups into Tableau through a Connected App JWT
  with `sub: HQ_integration_admin`.

So the access rules exist twice and can drift. Role mode collapses per-user rules to per-role
rules.

**Looker, Power BI, Superset and Metabase** are variations on the same two modes:

- **User-delegated embedding:** Looker SSO embed, Power BI "user owns data", Metabase
  interactive embedding.
- **Service principal with an app-held grant:** Power BI "app owns data" embed tokens,
  Superset guest tokens, Metabase static embedding.

They differ in token shape.

## The design, in one paragraph

An **embed client** is registered in labs: its signing keys (JWKS, asymmetric only), the origins
allowed to frame labs, and the **resources** it may show.

A resource is a `(kind, id)` pair. A kind is anything labs knows how to draw without chrome:
today a workflow (which covers pages, since a page is a workflow definition), later a supply
view, a microplan, a Pulse board or something not yet thought of. Nothing below is specific to
workflows except the one kind registered in phase 1.

The client's backend signs a short **RFC 7523
assertion** naming the viewer and the resource, and redeems it at labs' **embed token endpoint**
for a single-use bootstrap token. Its page frames
`https://labs…/embed/c/<client>/<kind>/<id>/` and hands the bootstrap token over (URL
fragment or `postMessage`, never the query string). Labs consumes it and sets a **partitioned**
cookie scoped to that one embed. Every request the frame makes is then authorised by an **embed
session**:

- It acts as the asserted user, with that user's own Connect token, so prod's access checks
  apply unchanged.
- It is confined to the routes its kind declares for the one resource it was issued for.
- It is never a labs login.

A workflow draws in the same sandboxed runner as everywhere else.

## Resource kinds

`connect_labs/embed/kinds.py` holds a registry of `EmbedKind`s. A kind declares five things,
and those five are all a new embeddable thing has to supply:

| member                                    | what it is                                                                                                   |
| ----------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `validate_entry(entry)`                   | is a client's resource entry well formed (checked when the client is saved)                                  |
| `scope(entry)`                            | the labs context the resource is read in (`opportunity_id` / `program_id`), from the client's resource entry |
| `check_access(user, access_token, entry)` | at token time, as the user: can they open it? Refuses with a reason the embedder sees                        |
| `render(request, session)`                | the chromeless view drawn in the frame                                                                       |
| `allowed(session, method, path)`          | the routes a session for this resource may reach                                                             |

The token endpoint, the session, the cookie, the middleware, the framing headers and the audit
trail are kind-agnostic. A kind cannot widen them. For example, the CSRF/Origin rule for POST
reads is the middleware's, not the kind's. Phase 1 registers `workflow`.

## Trust modes

| Mode                                                                              | Who decides what the viewer sees                                 | Status                                           |
| --------------------------------------------------------------------------------- | ---------------------------------------------------------------- | ------------------------------------------------ |
| `user`: the client asserts a labs/Connect user                                    | (client's resource list) ∩ (that user's prod access)             | **Phase 1**, for identity-authority clients only |
| `user` + consent: a third party asserts a user who has linked their account to it | same                                                             | Phase 3                                          |
| `app`: service principal, viewers unknown to labs                                 | the client's grant ceiling; the client's own ACL chooses viewers | Phase 3, generated/shared data first             |

**Identity authority** is the phase-1 trust rule. A client may assert _any_ labs user only if
labs already trusts it to say who people are. Connect is labs' identity provider (labs users
are created from Connect OAuth, `username` = Connect username), so asserting a Connect username
adds no new trust. Only Dimagi staff can mark a client as an identity authority. A client that
is not one cannot get a token in phase 1.

## Phase 1 (built): what labs serves

### 1. The registry: `embed.EmbedClient`

Edited in Django admin by staff.

- `client_id`: a slug, and the assertion's `iss`.
- `name`.
- `active`: false stops every outstanding session on its next request.
- `jwks_uri` (https, fetched SSRF-safe through `canopy_sdk.fetch`, cached ≤ 1 h, refetched
  once per 5 min on an unknown `kid`) **or** an inline `jwks`.
- `allowed_origins`: exact `https://host[:port]` origins. These become `frame-ancestors`.
- `identity_authority`.
- `resources`: a list of `{"kind": "workflow", "id": N, "opportunity_id": M}` (or
  `"program_id": P`). `kind` names a registered kind. The rest of the entry is that kind's
  to read: for `workflow`, the scope the definition is read in.
- `session_seconds` (default 3600, max 8 h).

Accepted algorithms: `EdDSA`, `ES256`, `RS256` (≥ 2048-bit). There are no shared secrets. A
symmetric key would let labs (or anyone who reads labs' copy) sign as the client.

### 2. The token endpoint: `POST /embed/token/`

This is RFC 7523 §2.1, a JWT used as an authorization grant. The assertion's signature is the
client's authentication.

```
grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer
assertion=<JWT>
```

Assertion claims:

| claim        | rule                                                                                      |
| ------------ | ----------------------------------------------------------------------------------------- |
| `iss`        | the `client_id`                                                                           |
| `sub`        | the viewer's Connect username                                                             |
| `aud`        | `<LABS_PUBLIC_URL>/embed/token/` (or `LABS_PUBLIC_URL` itself)                            |
| `iat`, `exp` | `exp - iat` ≤ 300 s, not expired (30 s leeway)                                            |
| `jti`        | unique; single use (canopy SDK's `SeenJti` table)                                         |
| `resource`   | `<LABS_PUBLIC_URL>/embed/c/<client_id>/<kind>/<id>/`, must be in the client's `resources` |

The header carries `kid` (required when the JWKS has more than one key) and `alg`. The
algorithm is checked against labs' list, never taken from the token.

Labs then checks:

1. The client is an identity authority (phase 1).
2. The user exists and is active.
3. Labs holds a Connect token for them (`UserConnectToken`). This is the phase-1 limit; see
   phase 2.
4. The kind's `check_access` succeeds **as that user**. For a workflow, that means two checks.
   First, labs' own scope check (`labs/access/scopes.may_use`) must pass. A labs-only
   (synthetic) opportunity's records are read with no permission check, so reading the
   definition alone proves nothing for one. Second, reading the definition in the resource's
   scope must succeed.
5. The assertion has not been used. Its `jti` is consumed only after every other check
   passes, so a refused request does not burn it.

The response is RFC 6749 §5.1 JSON with `Cache-Control: no-store`:

```json
{
  "access_token": "<bootstrap>",
  "token_type": "Bearer",
  "expires_in": 120,
  "embed_url": "https://labs…/embed/c/connect/workflow/123/"
}
```

Errors are RFC 6749 §5.2 (`invalid_request`, `invalid_client`, `invalid_grant`,
`unauthorized_client`, `unsupported_grant_type`) and RFC 8707's `invalid_target`. Every refusal is logged by reason and written to
the audit trail as `access_denied`.

### 3. The frame: `GET /embed/c/<client_id>/<kind>/<id>/`

The response carries an enforced `frame-ancestors <allowed_origins>` and no `X-Frame-Options`.
Every other labs URL keeps `DENY`.

- **No session yet:** a data-free bootstrap page. It takes the token from `#token=…`, or from a
  `postMessage({type: "labs-embed-token", token})` sent by a parent whose origin is in
  `allowed_origins`. It POSTs the token to `/embed/session/` and reloads without the fragment.
- **With a session that has not drawn yet:** the kind's view, without chrome.
  - **A session draws its frame document once** (`EmbedSession.drawn_at`). A second load (a
    reload, or the embedder's page opened again, perhaps by someone else) gets the bootstrap
    page and asks its parent for a new token. So a frame never shows an earlier person to
    whoever is signed in to the embedding system now.
  - The cookie keeps serving the reads of the document it drew.
  - For `workflow`:
    - It uses the same runner and sandbox as the workflow's own page.
    - It shows the newest run, or (for a page) no run.
    - A workflow with no runs says so. The embed never creates a run.

A token handed over by the parent always wins. A fragment or `postMessage` token on either page
starts a new session that replaces the frame's cookie.

The sandbox shell (`/labs/sandbox/render/`) sits inside the labs page, inside the embedder's.
`frame-ancestors` is checked against every ancestor, so the shell names every active client's
origins too. It is fixed and data-free, so that exposes nothing.

The frame posts `{type: "labs-embed", event: "ready" | "needs-token" | "error"}` to its parent,
so the embedder can re-mint when a session ends.

### 4. The session: `POST /embed/session/`

This step consumes the bootstrap token once, within 120 s, for the same client and resource. It
sets `labs_embed_<kind>_<id>`:

- `Secure; HttpOnly; SameSite=None; Partitioned` (CHIPS). The cookie lives only under the
  embedder's top-level site, so another site that frames labs gets no session.
- `Max-Age` = the client's `session_seconds`.

Only a checksum is stored (`EmbedSession`). Each resource's embed has its own cookie, so two
embeds on one page do not clobber each other.

### 5. Every request: `EmbedSessionMiddleware`

The middleware runs after the labs auth and context middlewares. When a request carries a
`labs_embed_*` cookie whose session is live, its client is active, and the path is on that
session's allowlist, the request is **replaced**, never merged:

- `request.user` becomes the session's user.
- `request.session` becomes an in-memory, never-saved session whose `labs_oauth` holds the
  user's current Connect token (`get_valid_access_token`) and their org list
  (`fetch_user_organization_data`, cached per person), as login would. The real session object
  is put back before `SessionMiddleware` sees the response.
- `request.labs_context` becomes the resource's scope.
- `request.labs_embed` is set to the session.

The allowlist is the embed page itself plus whatever the kind's `allowed` names. For
`workflow`, that is the runner's **read** endpoints for that definition and its runs:

- pipeline data, rows and streams
- the query endpoints
- the semantic layer
- supply and workflow sources
- run history and snapshots
- workers
- auth status

On any other path the embed cookie is **ignored**. It carries no authority there, and the
request is what it would have been without it (in another site's frame, anonymous). That
includes writes, other resources, the full labs pages, admin and the MCP. If the person's
Connect sign-in has lapsed, the embed's reads answer `401`, and the frame asks its parent to
start over.

The three read endpoints that are POSTs (`pipeline-query`, `supply-query`, `workflow-query`)
skip Django's CSRF check. A cross-site iframe has no CSRF cookie (it is `SameSite=Lax`). They
require `Origin` to be labs' own origin instead, which the frame's own `fetch` always sends and
a forged cross-site form cannot.

Phase 1 is read-only. Workflow actions (writes) from an embed are refused, and saved state
cannot change.

### 6. Audit

These are written to `audit_trail`, with `metadata.embed_client`:

- issuing a bootstrap token
- starting a session
- every refusal

The embedded page's own reads are audited as usual, attributed to the user, because the request
_is_ that user.

## Phase 2: users who have never signed in to labs (Connect builds this)

Phase 1 needs a stored Connect token for the viewer. For someone who has never opened labs,
labs must get one from Connect. Connect's `/o/token/` would accept the RFC 7523 jwt-bearer
grant from the labs OAuth client:

- **The assertion:** signed by labs with an ID-JAG shape (`typ: oauth-id-jag+jwt`). Its `sub`
  is the Connect username that Connect itself just asserted to labs. Its `aud` is Connect.
- **Client authentication:** `private_key_jwt`.
- **What Connect issues:** an `export` access token for that user, to the labs client.

django-oauth-toolkit does not ship this grant. Labs' implementation for canopy is
`canopy_sdk.host.grant.GrantHandler`, wrapped around DOT's `TokenView` by
`canopy_sdk.django.views.jwt_bearer_view`. It is about 150 lines Connect can copy. All of it
is standard: RFC 7523, RFC 7521, the IETF identity-chaining / ID-JAG draft, and RFC 7638 key
thumbprints. Connect is the identity provider and the resource server here, so this is identity
chaining, not a new trust relationship.

## Phase 3: third parties

**User mode with consent.** A non-authority client may assert a user only after that user has
linked their labs account to the client once (`EmbedLink`: user, client, `sub` in the client's
namespace, revocable at `/labs/embed/links/`). The token endpoint maps the client's `sub`
through the link.

**App mode.** The client is a principal. Its grant names resources and an optional row filter,
and the session runs as a per-client service user whose prod access is nothing. So app mode
shows only generated (`provenance.is_generated`) or explicitly shared data until a "labs
computes on behalf of a client" path exists.

## The other direction: Connect embedding third parties

Connect should build the **embedder** role once:

- a registry of embedded apps (their token endpoint, the `aud` they expect, their claim names)
- one key pair with a published JWKS
- a server-side "sign assertion, redeem, return embed URL + token" call
- iframes with `sandbox="allow-scripts allow-same-origin allow-popups"` and a narrow `allow=`
- a `frame-src` allowlist
- origin-checked `postMessage`

The assertion above is the common shape. Tableau's OAuth 2.0 trust Connected App accepts a JWT
signed by the host's JWKS with `sub`, `aud`, `scp`, `jti` and `exp`. Superset and Metabase JWT
embedding take similar claims. One issuer serves all of them, with small differences in `aud`
and the scope claim.

## Embedder quick start (Connect, phase 1)

```python
import time, uuid, jwt, requests  # PyJWT[crypto]

LABS = "https://labs.connect.dimagi.com"
resource = f"{LABS}/embed/c/connect/workflow/123/"  # <kind>/<id>
assertion = jwt.encode(
    {"iss": "connect", "sub": request.user.username, "aud": f"{LABS}/embed/token/",
     "iat": int(time.time()), "exp": int(time.time()) + 60, "jti": str(uuid.uuid4()),
     "resource": resource},
    PRIVATE_KEY, algorithm="EdDSA", headers={"kid": KEY_ID},
)
r = requests.post(f"{LABS}/embed/token/", timeout=10, data={
    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion})
r.raise_for_status()
grant = r.json()
# template: <iframe src="{{ embed_url }}#token={{ token }}" ...></iframe>
```

To register a client, send labs the public JWK (or a JWKS URL) and the origins that will frame
it. A labs staff member adds the client and its resources in Django admin (Embed clients).

## What is deliberately not done

- **No HS256 / shared secrets**, no IP-allowlist trust, no mirroring of users or roles into
  labs, and no long-lived signed URLs.
- **An embed session is never a labs login.** It cannot reach any route outside its allowlist.
  It is not carried by labs' session cookie, and it does not survive a client being disabled.
- **No login redirect inside the frame.** Connect's OAuth pages refuse framing. A frame with no
  session asks its parent for a token instead.
