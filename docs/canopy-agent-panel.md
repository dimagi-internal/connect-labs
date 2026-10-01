# The canopy agent panel in labs

A floating launcher on a labs page opens a chat with a canopy agent that can see
what the visitor is looking at. It is live on the marketplace network page, on
a round's page, on the targeting map (`/labs/targeting/`), and on the run page of
any workflow that opts in (`config.agent.share`, see
[Workflow run pages](#workflow-run-pages)). That is where it earns its keep: "draft
an email to each of these organisations for this EOI", "start OCS conversations
with everyone who is red" and "what would $50K of door-to-door ORS buy in Borno at
$2.50 a visit?" are questions about what is on screen.

## The targeting map

Registered as `targeting:index` with the `targeting:read` scope: every
`targeting_*` read tool, including `targeting_cost_effectiveness` (deaths averted,
cost per death and the multiple of GiveWell's bar for a round of ORS in one area).
Everything the page shows is public open data, so the scope adds no exposure; the
page state carries only the query parameters that describe the view
(`PANEL_FILTER_KEYS` in `labs/indicators/views.py`). The panel renders for
signed-in visitors only — the page itself is open to anonymous visitors locally.

Canopy's own guide is the authority on the widget
(`docs/architecture/embedding-a-canopy-agent.md` in `dimagi-internal/canopy-web`).
This file is only what is true of **labs**.

## How it hangs together

```
  the visitor's browser                  labs                     canopy
  ────────────────────      ────────────────────────      ───────────────────
  canopy_panel.html    ──►  POST /labs/canopy/token   ──► POST /api/auth/
    (widget.js, overlay)      signs an Ed25519            contact-token
                              assertion about
                              request.user                verifies the
                                                          signature, returns a
  ◄── short-lived token ◄───────────────────────────────  30-minute token
  setPageState(slugs)  ─────────────────────────────────► the agent reads rows
                                                          via marketplace_orgs_get
```

The protocol — signing, the mint view, the page token, the overlay template,
the jwt-bearer grant and the DPoP gate — is the **canopy SDK** (`dimagi-canopy`,
import `canopy_sdk`, from canopy-web `sdk/python`), the one implementation of
canopy's host grant contract that canopy itself tests against. Labs owns only
what is labs':

| Piece | Where |
| --- | --- |
| Page → scope and scope → tool registries, what labs vouches for, `CANOPY_HOST` built from labs' settings | `connect_labs/labs/canopy.py` |
| The mint and the published key (SDK views, labs' URLs) | `connect_labs/labs/urls.py` → `/labs/canopy/token/`, `/labs/canopy/jwks/`, `/labs/canopy/probe/` |
| The overlay | the SDK's `canopy_host/panel.html`, styled by `canopy.PANEL` |
| What the agent reads | `connect_labs/mcp/tools/marketplace.py` |

## Putting it on a page

Give the view a `canopy_panel`, and include the partial in
`{% block inline_javascript %}`:

```python
from canopy_sdk.django.pages import panel_context

context["canopy_panel"] = panel_context(
    request,
    resource="labs-marketplace://orgs",
    backing_tool="marketplace_orgs_get",
    visible_ids=[row["org"].slug for row in listed],
    filters={k: v for k, v in state["selected"].items() if v},
    path=request.path,
)
```

```django
{% block inline_javascript %}
  {% include "canopy_host/panel.html" %}
{% endblock %}
```

**`inline_javascript`, not `javascript`.** base.html renders the `javascript`
block inside `<head>`, where `document.body` is still null and the launcher has
nothing to attach to. The symptom is no launcher and nothing in the console
saying why. A test pins the ordering.

**Send the selection, never the rows.** `visible_ids` is slugs; the agent reads
the substance itself through `backing_tool`, live. Serialising rows into the page
state would duplicate the API, let the copy go stale between render and use, and
add a second place to get access control wrong. Canopy refuses a state over
8 KiB, and `panel_context` truncates at 400 ids before it gets that far — a
truncated selection still describes most of the screen, where a refused one
leaves the agent blind to all of it.

**Compute it from what this visitor can see.** "The agent sees exactly what the
user sees" is the access story, and keeping it true belongs to the page.

## Three things that are true of labs specifically

**The CSRF cookie is unreadable from JavaScript.** Labs sets
`CSRF_COOKIE_HTTPONLY = True`, so the widget's default (read the cookie) cannot
work at any cookie name. It is handed the rendered token instead, through
`csrfToken` — the same DOM read every other fetch in labs does. Canopy gained
that option for us (canopy-web#922). If the mint starts 403ing with no
`X-CSRFToken` on the request, that binding is what broke, and the `{% csrf_token %}`
at `base.html:76` is what it reads.

**A member of the workspace arrives as themselves.** Labs signs the visitor's
address with `email_verified: true` (it came from Connect's OAuth identity, which
is how they signed in). Canopy turns that into the person's own canopy account
only if exactly one canopy user holds that address verified AND is a member of
the workspace connect-labs is registered in — so they see their own chats and act
with their own access. Everyone else arrives as a canopy *contact*, which reaches
only the agents this site offers and their own conversations. Canopy never
creates an account from an assertion, and there is no domain list to configure
on either side.

**The page's resource picks what the agent may do.** A conversation on a labs page
runs in whichever capability of the agent's published interface names that page's
`resource` (canopy-web#942). ACE's `marketplace` capability names
`labs-marketplace://*` and carries `current_page`, the `marketplace_*` read tools,
`Skill`, and its Drive tools; everything else — including anyone who emails ACE —
stays in `ask`.

So **a new labs surface needs a capability, or the panel can only chat.** Declaring
a resource nothing matches falls back to `ask`, which for ACE is an email door: no
page tool, no `Skill`. That is how the first live run ended with the agent trying
`screencapture`. The interface is edited on the agent's canopy page, or with
`canopy agent interface get|set --slug <agent>` — never a file in the agent's repo.

Two things a capability cannot fix, learned the same way: a confined turn has no
`AskUserQuestion`, so an agent must ask in the chat; and a Drive write needs a
`parentFolderId` the agent can actually discover.

**Who the agent reads as.** With the delegated grant on (`CANOPY_CLIENT_ID`,
[below](#letting-canopy-act-as-the-visitor-off-by-default)), a registered page's
tools run **as the visitor**, limited to that page's scopes. Without it they run
with the agent's own labs credential, so the ids in the page state narrow what it
looks at but do not *limit* what it could look at. For the marketplace that adds no
exposure, since every labs user can already read the whole directory. Workflow run
pages need the grant: a run's data is scoped to the people with access to its
opportunities, and an agent reading as itself would see that person's run with
somebody else's access.

## Workflow run pages

A workflow shares its runs by turning it on for itself:
`config.agent = {"share": true}` (`connect_labs/workflow/agent_sharing.py`). What the
agent can then do is the workflow's own declared actions (`config.actions`,
`connect_labs/workflow/actions.py`), the same ones its buttons run. The full
contract is in `connect_labs/workflow/WORKFLOW_REFERENCE.md` §14. What is specific
to canopy:

- **Nothing about workflows is configured on canopy's side.** ACE's `connect`
  capability names the connect-labs SITE. A conversation on any labs page reaches
  labs' tools through canopy's gateway, as the visitor, and the agent learns what
  they are from labs' own tool list and descriptions. Labs alone decides what each
  page may do: the page's scopes (`PAGE_SCOPES`, here `workflow:read` +
  `workflow:act`) and the visitor's own access. Canopy's ceiling defers to that
  (`mcp__*connect_labs__*`), and after canopy-web#1031 canopy stops filtering by
  tool name altogether.
- **The selection follows the page.** When the page renders it declares the run,
  its own scope (`filters.run_id` and `opportunity_id` or `program_id`, which every
  run tool is called with) and the worker keys. When the report drills in, render
  code calls `view.shareSelection(...)`, which narrows `visible_ids` and sets
  `filters.drilled` through the SDK's `window.canopyHost.updatePageState` (SDK ≥ 0.5).
  `backing_tool` (`workflow_run_indicators`) is a hint about where to read, not a limit.
- **One page registration, every workflow.** `PAGE_SCOPES` is keyed by URL name, so
  the registration covers every run page. What limits it to workflows that share is
  the view: without `share` it renders no panel, so it issues no page token and no
  grant. Every `workflow_*` run tool also refuses a canopy call on a workflow that
  does not share, whatever the token carries.

| Scope | Tools |
| --- | --- |
| `workflow:read` | `workflow_run_context`, `workflow_run_indicators`, `workflow_indicator_explain`, `workflow_action_status` |
| `workflow:act` | `workflow_run_action` |
| `targeting:read` | every `targeting_*` read tool, incl. `targeting_cost_effectiveness` |

**The one write scope, and why it is safe to hand to a page.** `workflow_run_action`
cannot act in one call. Its first call is a preview of exactly what would happen,
with a single-use token bound to the visitor, the run and those arguments. Only a
second call carrying that token acts, and changing anything in between refuses it.
The tool tells the agent to show the preview and get the person's yes in between.
Labs enforces that a preview came first; it cannot enforce the yes itself, which is
what a canopy-side confirm (MCP `input_required`) would add. `PREVIEWED_WRITE_SCOPES`
names this scope, and `test_every_scoped_tool_exists_and_only_previewed_scopes_write`
holds any future write scope to the same rule.

## Contact details and transcripts

`marketplace_orgs_get` returns real people's names and email addresses when asked
— that is what drafting outreach needs, and the same fields are already on
`/labs/marketplace/org/<slug>/` for every labs user. Two rules:

- `include_contacts` is opt-in per call, so "what is in this round" does not drag
  PII through a transcript with no use for it.
- Nothing here should grow a field the org page does not already show. A
  transcript persists.

## Setting it up on a deployment

Steps 1–2 are one-off per environment.

**1. Connect the site in canopy**, as a workspace owner, at
`/canopy/w/<workspace>/settings/connected-apps`:

| Field | Value |
| --- | --- |
| Name | `connect-labs` (matches `CANOPY_APP_NAME`) |
| Site URLs | `https://labs.connect.dimagi.com` **and** `http://localhost:8000` |
| Agents it may offer | ACE and Eva |
| Where your site publishes its keys | `https://labs.connect.dimagi.com/labs/canopy/jwks/` |

All three fail closed, which is why "nothing happens" is almost always a missing
one rather than something broken.

**2. Generate a key pair.** The private half never leaves labs' server:

```bash
openssl genpkey -algorithm ed25519 -out canopy-signing.pem
```

**Give canopy the JWKS URL, not the key.** Labs publishes the public half at
`/labs/canopy/jwks/`, derived from the private key rather than configured
beside it — two settings that must agree are two that can disagree, and that
failure shows up as assertions verifying against nothing, far from the edit.

Rotation then costs nothing: put a new private key in Secrets Manager and canopy
follows by `kid` on its next fetch, refetching the moment it meets a `kid` it has
not seen. A key that can only be rotated by somebody re-pasting it is a key that
never gets rotated. Canopy requires the URL to be https, publicly reachable, free
of redirects and under 64 KiB.

(Pasting the public key into canopy's "…or paste a signing key" box still works
and behaves identically — the only difference is that each rotation means going
back there.)

**3. Set three env vars** (`deploy/task-definitions/*.json` for AWS — env vars
are wiped on deploy unless pinned there; see the `aws-env-update` skill):

```
CANOPY_BASE_URL=https://labs.connect.dimagi.com/canopy
CANOPY_APP_NAME=connect-labs
CANOPY_SIGNING_KEY=<the private PEM>      # Secrets Manager, never a plain env var
```

Newlines survive as literal `\n`; the settings module unescapes them.

The panel does not render at all unless all three are set. That is deliberate: a
launcher that opens onto an error the page cannot explain is worse than no
launcher.

## Letting canopy act as the visitor (off by default)

Without this, the agent's calls to labs' MCP run as the AGENT. With it, a page
can let canopy call labs' MCP **as the person on the page**, limited to what that
page offers. This is MCP's Enterprise-Managed Authorization shape (an ID-JAG
redeemed with the RFC 7523 jwt-bearer grant), and the principle is that labs —
which signed the person in — issues the grant; canopy only redeems it. Contract
and design: canopy-web PR #985
(`docs/superpowers/specs/2026-09-26-embedded-caller-delegation-design.md`).

1. **Which pages, which scopes** — `canopy.PAGE_SCOPES`, keyed by URL name. A
   page passes `request` to `panel_context`; the panel then carries labs'
   own signature over the route (`?page=` on the token URL). At mint time labs
   checks it and puts that route's scopes in an **ID-JAG** sent beside the
   visitor assertion. An unregistered page, or a missing/forged/expired page
   token, simply means no ID-JAG. v1: the network and round pages →
   `marketplace:read`.
2. **Redeeming** — canopy POSTs the ID-JAG to `/o/token/` with
   `private_key_jwt` (keys from its Client ID Metadata Document, fetched
   SSRF-safe and cached ≤ 1h) and a DPoP proof. Labs issues a 15-minute access
   token, no refresh token, bound to the DPoP key (the SDK's `canopy_host.DelegatedToken`, its
   own table — never django-oauth-toolkit's, which would open labs' REST API).
3. **Using it** — `Authorization: DPoP <token>` plus a fresh `DPoP` proof on every
   MCP request (the SDK's `DPoPGate`, mounted by `mcp.server.dpop_gate`). The
   tool runs as the visitor, and only the tools `canopy.SCOPE_TOOLS` maps the
   token's scopes to are listed or callable. `MCPAuditLog` records the client and the `Canopy-Actor` header.

PATs and ordinary MCP OAuth sign-ins never touch any of this.

**To turn it on**, set one more env var (with the three above and
`LABS_PUBLIC_URL`, which every JWT here names as issuer):

```
CANOPY_CLIENT_ID=https://labs.connect.dimagi.com/canopy/oauth/client.json
```

Unset, nothing changes: no ID-JAG is issued, `/o/token/` answers the grant with
`unsupported_grant_type`, and the metadata does not advertise it.

### canopy's live probe (on with the grant)

A broken grant used to surface only when a visitor asked an agent for something
and it could not do it: a real grant needs an ID-JAG signed by labs' key, which
canopy never holds. So labs offers canopy a **probe** (SDK 0.4.0): canopy POSTs
to `/labs/canopy/probe/`, authenticated as its client exactly as at `/o/token/`
(`private_key_jwt` + a DPoP proof), and gets back a real ID-JAG for ONE fixed
principal, which it redeems at `/o/token/` and uses at `/mcp/` — every step a
visitor's grant takes. canopy runs it every 30 minutes and on **Test
connection**, and checks that (a) the probe tool succeeds, (b) a tool outside
the scope is neither listed nor callable, and (c) the call without a valid DPoP
proof is refused.

| | |
| --- | --- |
| Principal | `canopy:probe` — a service account, never a person. Created by `mcp/migrations/0006` (the deploy runs `migrate` whenever a migration changes). Active, no usable password, no staff bit, no email, no PAT; the `:` is a character no Connect username can have, and the OAuth callback refuses the name besides |
| Scope / tool | `marketplace:read` → `marketplace_rounds_list` with `{"open_only": true}`: no per-user gate, so it succeeds for the probe user; an empty list is still a success |
| Denied tool | `list_templates` — a real read tool outside the scope |
| Page | `marketplace:network` (audit only) |
| Endpoint | `{LABS_PUBLIC_URL}/labs/canopy/probe/` — must be the public URL exactly (a DPoP proof's `htu`), and is advertised as `canopy_probe_endpoint` in `/.well-known/oauth-authorization-server` |

All of it is in `connect_labs/labs/canopy.py` (`PROBE_*`, `probe_subject`), and
none of it is a secret. It is **on wherever the grant is** and needs no env var
of its own: without `CANOPY_CLIENT_ID` the endpoint 404s, and it 404s too until
the probe user exists. MCP calls made with a probe token land in `MCPAuditLog` as
`canopy:probe`, with canopy's client id. To stop it, deactivate the user (canopy
then reports a refused probe rather than an unconfigured one).

## When it does not work

| Symptom | Cause |
| --- | --- |
| No launcher at all | one of the three settings is unset — the include renders nothing |
| Every mint 401s with `bad_signature` | canopy cannot reach `/labs/canopy/jwks/`, or it is serving `{"keys": []}` because `CANOPY_SIGNING_KEY` is not a readable PEM |
| Launcher, then a token error | the mint 403'd (CSRF binding), 502'd (canopy unreachable) or 401'd (signature). Labs logs canopy's own words; the browser deliberately does not get them |
| Blank panel, console says a frame refused to load | labs' origin is not in the site's **Site URLs** |
| "No agent is available here yet" | no allowed-agents row, or the agent is not in that workspace |
| The agent knows nothing about the page | `resource` not declared, or the panel rendered in `<head>` |
| A message sends, no reply ever arrives | no runner online for that agent — canopy-side, not labs |

## Related

- `connect_labs/marketplace/README` territory: the directory is the master org
  registry, fed from the LLO Directory sheet by `marketplace_import`. These tools
  are read-only because that sheet is the source of truth.
- canopy-web#922 (the CSRF option), #925 (page state for contacts), #928 (the
  assurance grade an embedded site cannot earn).
