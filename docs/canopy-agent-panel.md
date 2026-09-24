# The canopy agent panel in labs

A floating launcher on a labs page opens a chat with a canopy agent that can see
what the visitor is looking at. It is live on the marketplace network page and on
a round's page, which is where it earns its keep: "draft an email to each of
these organisations for this EOI" is a question about the rows on screen.

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

Four labs-side pieces:

| Piece | Where |
| --- | --- |
| Signing + the mint call | `connect_labs/labs/canopy.py` |
| The one endpoint, plus the published key | `connect_labs/labs/canopy_views.py` → `/labs/canopy/token/`, `/labs/canopy/jwks/` |
| The overlay, and the page it declares | `connect_labs/templates/labs/includes/canopy_panel.html` |
| What the agent reads | `connect_labs/mcp/tools/marketplace.py` |

## Putting it on a page

Give the view a `canopy_panel`, and include the partial in
`{% block inline_javascript %}`:

```python
context["canopy_panel"] = canopy.panel_context(
    resource="labs-marketplace://orgs",
    backing_tool="marketplace_orgs_get",
    visible_ids=[row["org"].slug for row in listed],
    filters={k: v for k, v in state["selected"].items() if v},
    path=request.path,
)
```

```django
{% block inline_javascript %}
  {% include "labs/includes/canopy_panel.html" %}
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

**The agent reads as itself, not as the visitor.** Its tools run with its own
labs credential, so the ids in the page state narrow what it looks at but do not
*limit* what it could look at. Every labs user can already read the whole
directory including contacts, so this adds no exposure between labs users — but
it does mean an embedded agent should only ever hold access that is fine for
every visitor who can reach it. Canopy has an on-behalf-of assertion
(`/api/tokens/on-behalf-of/jwks`) that would let labs run a tool call *as* the
visitor; verifying it in labs' MCP server is the next piece of work, and until
then this paragraph stands.

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
