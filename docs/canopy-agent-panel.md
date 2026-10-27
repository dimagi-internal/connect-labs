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

| Piece                                                                                                    | Where                                                                                            |
| -------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| Page → scope and scope → tool registries, what labs vouches for, `CANOPY_HOST` built from labs' settings | `connect_labs/labs/canopy.py`                                                                    |
| The mint and the published key (SDK views, labs' URLs)                                                   | `connect_labs/labs/urls.py` → `/labs/canopy/token/`, `/labs/canopy/jwks/`, `/labs/canopy/probe/` |
| The overlay                                                                                              | the SDK's `canopy_host/panel.html`, styled by `canopy.PANEL`                                     |
| What the agent reads                                                                                     | `connect_labs/mcp/tools/marketplace.py`                                                          |

## Sensitive data: no agent unless labs has cleared it

Labs' **sensitive data** is production visit data (it carries PII and PHI), as opposed to
GENERATED synthetic data. **Over sensitive data no agent panel is drawn** unless labs has
cleared the agent. Every page states what the
agent could read through it, and `connect_labs/labs/canopy_panel.py` decides from that
whether an agent may be offered at all:

| The page reads                                                                                                                                    | Agent offered                                     |
| ------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------- |
| no visit data (`NO_VISIT_DATA`: the directory, targeting, a tender, the Data page -- whose `data_*` tools give an uncleared agent structure only) | yes                                               |
| visits of opportunities that all hold GENERATED synthetic data (`provenance.is_generated`)                                                        | yes, any agent                                    |
| any production visits (sensitive data), or it cannot tell                                                                                         | only an agent labs has cleared for sensitive data |

**Which agent, and whether at all, is one answer**: `canopy_panel.availability(slug, reads)`.
It combines labs' ticks with canopy's signed word on the agent, and the panel, the run
page's Agent box (greyed choices and the red "not shown on this page" line) and the Labs
Admin table all read it, so none of them can disagree or fall silent.

- **canopy's word** is the site agents API (`GET {canopy}/api/sites/agents`, dimagi-canopy
  0.14, `canopy_sdk.django.site_agents.current()`, read through
  `canopy_settings.site_agents()`): the agents canopy allows on labs' connected site,
  signed with canopy's client key, each with whether its author declared it handles
  sensitive data, whether it has a ready sensitive-data runner, whether it is ready, and canopy's own problems.
  Cached five minutes; when canopy is unreachable its last word is used for up to a day,
  then **no agent is shown anywhere** (fail closed).
- **labs' ticks** (below) say which of those labs shows, which may work with sensitive data, and the
  default agent.
- An agent canopy declares handles sensitive data, or that labs lets work with sensitive data, is
  always offered **on sensitive-data runners, without the run's actions**: page key
  `labs:workflow:run:data` (`workflow:read`, `data:read`, `workflow:edit`, never `workflow:act`)
  and a docked panel. It is not read-only: it may propose edits to the workflow and its
  pipelines, which the person confirms in chat and which stay in labs; the data goes nowhere else.

Over sensitive data an agent needs, failing closed on each: "Sensitive data" ticked in labs;
canopy's word that it handles sensitive data with a ready sensitive-data runner (a ZDR API
key, a locked harness, egress limited to an allowlist, nothing kept on the box; so drift in
canopy withdraws it at once); every conversation with it required to run on sensitive-data
runners (signed into its page token);
and canopy's per-call `Canopy-Execution` statement (canopy-web spec
`2026-10-22-sensitive-data-agents-design` §6). Labs verifies that statement once per
delegated tool call (`mcp/execution.py`) and holds the call to it
(`canopy_panel.call_sensitive_data_clearance`): the agent it names has "Sensitive data" ticked, is
declared to handle sensitive data (`data: sensitive`), on a runner with `sensitive_data` in
force in sensitive-data mode, in a conversation that requires `sensitive_data`. A call is held to the statement, never to the
cached listing. Each delegated call's statement (or why there was none) is recorded on
`MCPAuditLog.execution`. With nothing ticked (the default), **no agent is offered over
sensitive data** -- a sharing workflow over production opps shows its own buttons and no
launcher.

**Labs' side is set in the UI**, at Labs Admin › Canopy agents
(`/labs/admin/canopy-agents/`, `labs/canopy_settings.py`): a table with one row per agent
canopy lists (Agent, What it does here, Handles sensitive data, Sensitive-data runner, Ready -- canopy's
columns --
then labs' ticks: Show on labs, Sensitive data, Default), plus the runner flags every
conversation must run on, chosen from the flags canopy names. Nothing is typed. "Sensitive
data" -- the agent may work with Labs' sensitive data (production visit data) -- can be
ticked only when canopy says the agent handles sensitive data with a ready sensitive-data runner; an
agent labs still ticks that canopy no longer lists, or no longer clears, is shown in red
with canopy's reason and is not offered. Labs Admin users can read it; Dimagi staff (a
Dimagi sign-in, `is_dimagi_staff`) change it. Each change is a new `CanopyAgentSettings`
row (who, when, why, the site agents `version` it was made against, audited) and any
earlier one can be restored. No environment variable chooses an agent: until someone
saves there, **nothing is set up** -- no agent is offered on any page -- and the page
says so. It also holds the switch for [canopy acting as the
visitor](#letting-canopy-act-as-the-visitor-off-by-default), a **Refresh from canopy**
button (asks canopy for its list now instead of waiting out the five-minute cache, and
says what came back), and a warning when canopy's list has changed since the settings
were saved (its `version` differs from the saved one). canopy's problem sentences are
shown with their links. The page also lists labs' checks for the agent for data work and
the last delegated `data_*` calls with what canopy's statement said about each.

**Labs tells canopy what it needs**, too: `/labs/canopy/site.json` (the SDK's
`site_document`, beside the JWKS, signed with the same key) lists labs' pages and the
scopes each grants (`PAGE_SCOPES`, titled by `PAGE_TITLES`), what sensitive data requires
(`sensitive_data.requires`: `handles_sensitive`, runner flag `sensitive_data`), and which
agents labs shows and lets work with sensitive data (`uses.agents`,
`uses.sensitive_data_agents`; `canopy.site_needs`, dimagi-canopy 0.17). canopy's Connected
sites page reads it and shows, per agent, whether it meets labs' needs. Canopy agents do
not need to know labs' pages: labs describes them in `site.json` for reference, and
enforces each page's scopes itself.

Not drawn means no page token, no grant and no ID-JAG. Labs' MCP holds canopy's calls to
the same rule independently (`mcp/visit_access.py:delegated_denied_reason`, every tool
under `canopy.VISIT_SCOPES`), so a token minted before an opportunity's data changed is
refused too. A person's own agent (a PAT or MCP sign-in) is not affected.

## Putting it on a page

Give the view a `canopy_panel` from **`canopy_panel.panel`** -- never the SDK's
`panel_context` directly (a test pins that) -- saying what the page reads, and include
the partial in `{% block inline_javascript %}`:

```python
from connect_labs.labs import canopy_panel

context["canopy_panel"] = canopy_panel.panel(
    request,
    reads=canopy_panel.NO_VISIT_DATA,  # or canopy_panel.visits(opportunity_ids)
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

## The agent for data work (labsdata), on the Data page and on workflows

The Data page (`/labs/data/`, `connect_labs/labsdata`) offers the **agent for data work**
(`canopy_settings.data_agent`): the first agent labs lets work with sensitive data, else the first
shown agent canopy declares handles sensitive data (`labsdata`). With none, the Data pages offer no agent at all: they never fall back to the default agent or to an ordinary runner.
The page passes `canopy_panel.data_page_agent()` -- that agent, `runner_requirements:
["sensitive_data"]` and a docked, expandable panel -- and the SDK signs the agent and the requirement
into the page token. Canopy then binds the visitor's token and the host grant to that
agent, so another agent cannot spend the `data:read` grant the Data page minted, and the
conversation runs only on sensitive-data runners.

**The agent on a page reads only what the page shows** (`labsdata/page_holds.py`). A
`data:read` grant would otherwise let the `data_*` tools read anything the visitor holds (the
harness caught the agent on a KMC run page reading another programme and offering a combined
sum). So every `data_*` call under a page grant is held to that page's selection: a workflow
run page to the workflow's opportunities and its pipelines, a data space's page to its space,
the plain Data page to the selection on screen. A call outside it is refused
`PERMISSION_DENIED` with `reason: outside_page` and a message telling the agent to send the
person to the Data page. Where it comes from is labs, never the agent: `canopy_panel.panel(...,
holds=)` signs the selection onto the panel's token URL (bound to that page token and person);
labs' mint view (`labs/canopy_token.py`, the SDK's `panel_token` plus this record) puts an
ID-JAG in a mint only when the panel asks for one (`{"grant": true}`, sent once the person has
sent a message, never on page load; canopy then holds it unusable until they confirm it with
their passkey), and for that grant stores a
`PageHold` for the page and a `PageGrant` for the ID-JAG's `jti`; the delegated token canopy
redeems keeps that `jti` (`grant_jti`), so the MCP finds the call's page from its own token
(`mcp/server.py` puts it in the claims). On the plain Data page the person changes the
selection in place, and the page reports it in their own session (`/labs/data/api/page-holds/`,
CSRF, every source resolved as them). Only the person's own gesture reaches that report: in
`static/labsdata/data.js` `reportSelection` takes the DOM event of their click and refuses
anything else, and no page action holds one -- an action that names other sources (`openItem`
on a personal saved query, `data_set_query` with sources) shows them in a canvas tab over those
sources and leaves the page's selection, and so the agent's reach, as it was (`data.test.js`
pins both, and that no action's code names the selection). A delegated call whose grant has no
hold reads nothing.
A person's own PAT or OAuth sign-in has no page and is not held.

A workflow chooses which agent its run pages offer (`config.agent.agent`, a slug, the run
page's **Agent** box; WORKFLOW_REFERENCE.md §14) from the agents Labs Admin shows, so
different workflows can offer different agents. An agent that works with sensitive data
(above) reads the run, and changes the workflow only by an edit the person confirms in chat
(below). Every agent
gets the author's `brief` through `data_workflow_context`. A choice that is not available
offers no panel, and the Agent box says why.

## Results labs keeps: facts for the agent, the rows in labs (`labs-result://`)

Keeping a tool's result is labs' own MCP feature, plain MCP that any client uses (Claude
Code, ACE, canopy); canopy is one client of it. A tool that produces rows does not put
them in its result. `data_query`, `workflow_run_indicators` and `targeting_pmc_states`
keep what they produced as a **kept result** in labs' database (`mcp.KeptResult`, table
`mcp_kept_result`; `connect_labs/mcp/kept_results.py`) and return **facts** -- the row
count, the columns, a sample, what was asked (the SQL) -- plus an MCP `resource_link`
(`labs-result://<id>`, also `result_uri` in the JSON). canopy carries the link, never the
rows; in a sensitive conversation the link is all it stores, and `site.json` declares the
`labs-result` scheme (`CANOPY_HOST["RESOURCE_SCHEMES"]` in `labs/canopy.py:host_settings`,
published as `site.resource_schemes`) and its 24-character ids (`RESOURCE_ID_LENGTHS`,
`site.resource_id_lengths`, SDK 0.22+) so canopy keeps it -- as `labs-result://` and
exactly such an id, nothing longer. That declaration is the only
part of this that goes through the canopy SDK.

- **Who gets what inline.** A canopy agent gets the first `INLINE_ROWS` (20) rows as
  `sample_rows` (`sample_is_partial` when there are more): most aggregates arrive whole,
  and a long result arrives as a sample it reads further with canopy's `site_read`, a page
  at a time. Every other caller -- Claude Code, ACE, scout-queries -- has no `site_read`
  and parses rows, so it gets every row inline and **nothing is kept**: labs keeps a copy
  of production rows only for a caller that reads it back (canopy, or a client passing
  `keep_result: true`, which also gets the link) -- `data_query`'s and
  `workflow_run_indicators`' alike. `explorer_query` (scout-queries' name)
  keeps nothing and is unchanged. A result over `KEPT_RESULT_MAX_BYTES` (5 MB of JSON) is
  not kept: the call is refused with "ask for fewer rows".
- **Reading one back** is MCP `resources/read` on labs' MCP server (the resource template
  `labs-result://{id}{?page,page_size,sort,dir}`, registered by `kept_results.mount` and listed by
  `resources/templates/list`): a table pages through the URI (default 100, max 1000 rows a
  page) with `_meta["labs/result"]` saying the kind, the page, pages, row count, expiry and
  that it is the **snapshot** the agent saw (the card and the agent agree on the rows). A
  refusal is an MCP error `"<code>: <why>"` whose `data` is `{"code": ...}` (`kept_results.REFUSALS`:
  `bad_uri`, `not_found`, `expired`, `forbidden`, and `unavailable` when Connect could not
  say whether the reader may -- still refused, but not "not yours"; `"sensitive": true` on
  labs' sensitive-data rule).
  Who may read is decided before expiry, so someone who may not read a result never
  learns whether it has expired. A cleared agent's page (`site_read`) is cut as its
  query was (`labsdata.service.shape_for_agent`: 4,000 characters a cell, 200,000 a page),
  so it cannot pull tens of megabytes of `form_json` through one read; the person's card
  pages the rows whole.
- **Who may read it.** The labs user the call ran as (the owner, read from the MCP
  request's token), and anyone who could run the same thing themselves (`may_read`): holds
  every opportunity the query read, can open the run the indicators came from; a public
  PMC ranking, any labs user. Nobody else (`forbidden`).
- **What a read may carry** (`kept_results.read_denial`, every reader, the owner too): a
  result holding sensitive data (`KeptResult.sensitive`) goes back through canopy only to
  an agent whose own `Canopy-Execution` statement clears it, or to the person's own card --
  which canopy states with a verified `Canopy-View` for this result and this person -- for
  a result a cleared agent already received (`through_canopy`). Any delegated read that
  is not a verified View is an agent's: cleared, cut and audited as one. A "no user visit data" caller never reads one. Every read,
  and every refused read (`outcome` failure, with the refusal), is on the audit trail
  (`resource_type` `labs_result`, `source` `mcp`, the MCP request id, `path`
  `mcp:resources/read`, the token's `auth_method` and client, and for a canopy call the
  `Canopy-Execution` facts).
- **For a day** (the public PMC ranking, which holds no visit data, for 30 days). Expired
  results read as `expired`; `connect_labs.mcp.tasks.sweep_kept_results` (beat
  `sweep-kept-results`, hourly) deletes them. RDS snapshots and point-in-time recovery
  keep a deleted row for the backup window (`docs/AUDIT_LOGGING.md`).
- **Never changed.** Labs does not update a kept result, so it never sends canopy a
  "result changed" push (the SDK's `canopy_sdk.resources.notify_updated`), and no card
  subscribes to one. A future tool that edits one in place would send that push and make
  its card subscribe.

**The results card** (`ui://labs/data-result`) reads the link as its viewer, pages on the
server, and never runs the query itself. Sorting a column of a result with more than one
page asks Labs for the whole result in that order (`?sort=<column>&dir=asc|desc`,
checked against the result's columns, done in SQL over the stored JSON before the page is
cut: `kept_results.page_of`); a one-page result sorts in the card. It names each refusal:
expired ("ask again
in the chat" -- showing the sample the tool result carried, said plainly to be a sample,
when it has one), not yours, Connect could not confirm access (try again), or sensitive
data that stays in Labs (with the Data page), deciding on the refusal's `data.code` (or,
from a host that passes only the message, the code at the head of Labs' message).

What else the card shows comes from the kept result, never from the tool input (a
sensitive conversation may keep none):

- **The agent's display** (`data_query`'s `display`, `labsdata/display.py`): plain-text
  column labels (the raw name on hover), number formats from an allowlist
  (`number_N`, `percent_N` over a fraction, `currency_N`, `compact_N`, `date`) and a chart
  (`bar`, sorted largest first; `line`; `stat`, one headline number). Checked strictly --
  every column named must be in the result -- and refused with what is wrong.
- **Provenance**: the SQL laid out to read (`sql_formatted`, sqlglot's pretty printer;
  `sql_executed` stays as it ran) and "Data as of 3 h ago" from each opportunity's
  `cached_at` (the oldest when there are several, the rest on hover).
- **Charts it picks itself** when the agent gave none: no id- or year-like column as a
  measure, never a percent beside a count, bars largest first, at most five series.
- **Download CSV** and **Save** open the Data page (`service.open_on_page_url`: the exact
  SQL, the opportunities and the row cap) with `ui/open-link`, in a new tab under the
  person's own Labs session. The page runs the query again there, live, as the person
  (`&run=1`), and highlights Download CSV (`&download=1`) or Save query (`&save=1`, with
  the display); nothing downloads or saves without a click. So a download is current data,
  not the 24-hour snapshot, and no download or save path exists outside the Data page:
  no URL an agent could use, and no way around the sensitive-data clearance. The page's
  CSV guards against formula injection and starts with a BOM for Excel.

The agent's facts carry `column_stats` per column (type, nulls, distinct count capped at
1,000, min/max/mean of a number, min/max of a date, and the top five values of a
categorical text column), under the rule its sample follows: an agent that is not
cleared for sensitive data gets no rows and so no statistics, and a text column's values
are named only as `data_describe` names them (at most 12 distinct values, each seen at
least 5 times), never a min or max of text.

**Saved queries** (`labsdata.models.SavedQuery`) are the Data page's own: the SQL, the
opportunities, the row cap and the display, never rows. Saving is the person's write in
their own session (CSRF), over opportunities they hold now, with SQL that passes the
checks; saving a name again adds a version. A personal one ("My queries") is listed,
opened and deleted only by its owner; one saved into a **data space** belongs to the space
(below). Opening one runs it again as the person. The kept result's 24-hour life is
unchanged. A card's Save lands on the Data page with the Save dialog open on the space whose
sources match the card's selection (or My queries); a query run over a space links to the
space's page (`/labs/data/space/<id>/`).

## Data spaces on the Data page

A **data space** (`labsdata.models.DataSpace`, `labsdata/spaces.py`; design:
`docs/superpowers/specs/2026-10-24-data-spaces-design.md` and
`docs/superpowers/specs/2026-10-24-one-path-through-data-spaces.md`) is a fixed set of sources --
opportunities, programs frozen to the opportunities they had when added, or one workflow -- and
the saved queries kept on it. Every read on the Data page and through the `data_*` tools is over
one: with no space open, the page's selection is an unsaved space generated for each call
(`spaces.generate`), and "Save as a space" keeps it. Who may open a saved one is decided on every
read by `spaces.open`, the one
gate (`labsdata/tests/test_spaces_gate.py` fails if a view or `data_*` tool takes a `space_id`
without it): every program AND every frozen opportunity held now, else `not_found` (the same
answer as no such space). No membership table and no grant: a teammate holding the same sources
sees it; losing one opportunity loses the whole space.

**Who can open it, and who changes it** (the ACL; design doc's last section). Access is two
rules, both checked by `spaces.open` on every load and read: the data rule above (fixed), then
the space's `audience`, chosen by its managers, which can only narrow it -- `holders` (default:
anyone who passes the data rule) or `private` (only its managers). Each audience is one function
in `spaces.AUDIENCES`, asked only after the data rule; refused by it is the same `not_found`, and
`openable` lists only spaces passing both. **Managers** (the creator, and anyone a manager adds,
`DataSpace.managers`) change the sources, audience and managers, archive the space and delete
any item; **members** (anyone else who can open it) run, save new items and versions, and delete
items they saved. `spaces.py` checks the role itself (`require_manager` → 403 `not_manager`), and
a manager is held to both rules like anyone else, so one who loses a source cannot manage either.
A manager adds someone by username or email, who must pass the data rule (asked as them); any
refusal is one `not_eligible`, and the last manager cannot be removed (`last_manager`). A labs
admin (Dimagi staff, `is_dimagi_staff`) can make someone who can open the space a manager
(`POST /labs/data/api/spaces/<id>/admin/managers/ {person}`) without holding it; nobody can make
themselves one. Sources (`.../sources/`, with `confirm: true`), audience (`.../audience/`) and
managers (`.../managers/`, `.../managers/<user_id>/remove/`) are person-session JSON views behind
CSRF, audited with before and after; **no `data_*` tool and no page action names any of them**
(`test_spaces_gate.py` pins that, and that every such route makes the checked write). The left
rail's "Who can open" card shows a manager the controls and a member the same facts read-only.

**Saved conversations** (design doc's "Saved conversations"; `models.SpaceConversation`) are
pointers to canopy chat sessions kept in a space: canopy's session id, a label the saver typed
(default "Conversation, <date>", never canopy's title), who saved it and when. No transcript is
stored. They follow the space's rules (`spaces.open`; any member saves, a member deletes their
own, a manager any) and a pointer grants nothing: clicking one in the rail's "Conversations" card
calls `canopyHost.openSession(id)`, and canopy opens it as the person under its own session ACL
(someone not in a private one is offered to ask to join). "Save this conversation" saves the
conversation the panel reports open (`canopy:session` / `canopyHost.session()`, dimagi-canopy
0.22.1; `{id, resource, private}`), only when its `resource` (where canopy says it was started)
is this space's `labs-data-space://<id>` (`not_this_space` otherwise). Routes:
`GET|POST /labs/data/api/spaces/<id>/conversations/`, `POST .../conversations/<pk>/delete/`. No
`data_*` tool and no page action reaches them.

Its page (`/labs/data/space/<id>/`, page key `labsdata:space`, `data:read`) is the Data page in
three regions: the left rail (space switcher and "Save as a space", source chips
with Update sources, tables, the space's queries with versions, My queries), a canvas with a tab
strip (the editor and the last few things shown), and the docked panel (`DATA_PANEL`; the
widget's own pin snaps it into the page, its expand control grows it). The panel's resource is
`labs-data-space://<id>` and its page state `filters.space_id`; every `data_*` tool takes
`space_id`, reads exactly the space's sources, and refuses any other selection beside it
(`outside_space`). The real-row rules (`agent_sees_sensitive_rows`, `Canopy-Execution`) are
unchanged.

**Page actions** (registered by `static/labsdata/data.js`, so the agent sees `page_<name>`; each
runs in the person's tab, as them, checks its input and throws to refuse, and returns an
acknowledgement only -- never rows, counts or a query's error text):
`showQuery {sql, title, display}` runs a query in a new canvas tab; `openItem {kind: "query", id}`
opens a saved query of the space (any version) or of the person's own -- in a new canvas tab,
a personal one over its own saved selection, never as the page's selection; `proposeSave {name, sql,
display}` fills the Save dialog, saving nothing; `showVisit {visit_id}` opens a visit; the older
`data_set_query` still puts SQL in the editor (given sources of its own, it runs in a tab over them
instead). No page action changes the page's selection. `data_describe` teaches a canopy agent these
(`guidance.PAGE_ACTIONS`, and `guidance.SPACE_RULES` with a `space_id`).

The PMC
card (`ui://labs/pmc-states`) reads its ranking the same way and re-totals ticked states
through `targeting_pmc_rank` as the viewer. The coaching preview and the edit-confirm
cards deliberately preview again as the viewer: their confirm token must be the viewer's
own, never the agent's.

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
with their own access. Everyone else arrives as a canopy _contact_, which reaches
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

**Who the agent reads as.** With the delegated grant on (ticked at Labs Admin › Canopy agents,
[below](#letting-canopy-act-as-the-visitor-off-by-default)), a registered page's
tools run **as the visitor**, limited to that page's scopes. Without it they run
with the agent's own labs credential, so the ids in the page state narrow what it
looks at but do not _limit_ what it could look at. For the marketplace that adds no
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

| Scope            | Tools                                                                                                                                |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `workflow:read`  | `workflow_run_context`, `workflow_run_indicators`, `workflow_indicator_explain`, `workflow_action_status`                            |
| `workflow:act`   | `workflow_run_action`, `workflow_action_preview_view` (app-only: the coaching View's own preview)                                    |
| `workflow:edit`  | `workflow_edit_propose`; app-only, for the confirm card: `workflow_edit_review_view`, `workflow_edit_apply`, `workflow_edit_decline` |
| `targeting:read` | every `targeting_*` read tool, incl. `targeting_cost_effectiveness`                                                                  |

**Edits, confirmed in chat (`workflow:edit`).** The agent never calls an edit tool
(`workflow_update_definition`, `workflow_patch_render_code`, `pipeline_update_schema`, the
`semantic_registry_*` writes …). It proposes one: `workflow_edit_propose` checks it, works
out the diff and holds it unsaved (`StagedEdit`). canopy draws the confirm card
(`ui://labs/workflow-edit-confirm`), which reads the edit back as the viewer, with a
single-use token only the card gets, and saves it on their Confirm by running the edit
tool as them -- straight to the workflow, whose version history is the undo. Refused
whatever the person confirms, because they would send data elsewhere: anything but the
listed edit tools (sharing, publishing, cloning, deleting), a definition's `kind`, `page`,
`workflow_sources`, `config_reads`, `config.actions`, `config.agent` and `config.sandbox`,
and render code on a workflow that does not run in the page sandbox. Held to the page, too
(`labsdata/page_holds.py` `run_denial`): a proposal changes the page's own workflow, a
pipeline it reads, or the registry it computes from, and a new pipeline or registry is
created only in the workflow's own scope (never shared). Code: `mcp/tools/workflow_edit.py`.

**Definition reads are held to the page too.** Under a page grant, `data:read`'s definition
tools -- `workflow_get`, `pipeline_get`, `pipeline_sql`, `semantic_registry_get` /
`_explain` / `_list` -- read only the page's own workflow (in its own scope), the pipelines
it reads, and the registries it computes from or whose deployment covers the page's
opportunities (what `data_describe` lists); `semantic_registry_list` lists only those. They
are read as the person when the call is made (`page_holds.page_definitions`). A person's own
PAT or sign-in is not held.

**App-only tools run on canopy's word, not on trust.** A tool only a View may call
(`_meta.ui.visibility == ["app"]`: `workflow_action_preview_view`, `workflow_edit_review_view`,
`workflow_edit_apply`, `workflow_edit_decline`) runs under a delegated token only with a
verified `Canopy-View` statement for THAT `tools/call` -- its `uri` the View's own `ui://`
resource, its `rpc` that very request, its `sub` the person the token serves
(`mcp/server.py` `view_denial`; verified once per call in `mcp/execution.py`). No
statement, an agent's `Canopy-Execution`, or a statement for another call or View is
refused before the tool runs. canopy must therefore send `Canopy-View` on a View's
`tools/call`, not only on its `resources/read`.

**The coaching write scope, and why it is safe to hand to a page.** `workflow_run_action`
cannot act in one call. Its first call is a preview of exactly what would happen,
with a single-use token bound to the visitor, the run and those arguments. Only a
second call carrying that token acts, and changing anything in between refuses it.
**Through canopy every action is the person's click.** Each action writes as the person (a
follow-up task in their name, a coaching conversation from their Open Chat Studio), so
under a page grant none is the agent's to confirm: its preview carries no `confirm`
(`sent_by: click`), the View (the card) gets the token and shows the action's own button,
and the commit runs only with the View's `Canopy-View` (`workflow_run.click_only`). The
run page's own button is the other way. Only a person's own agent (their PAT or sign-in)
confirms a non-coaching action in chat, on their yes.
**Coaching is sent by a click, never by the agent.** A `start_ocs_outreach` preview,
as the agent gets it, carries no `confirm`. `workflow_run_action` is an MCP Apps tool
(`_meta.ui.resourceUri = ui://labs/workflow-action-preview`): canopy renders its result
as a View, which previews again as the person looking at it through the app-only
`workflow_action_preview_view` (the picture inline, that person's own token) and sends
on their click. The run page's Start coaching button is the other way to send. See
`connect_labs/mcp/ui/` and canopy-web's `2026-10-08-mcp-apps-host-design.md`. `PREVIEWED_WRITE_SCOPES`
names this scope, and `test_every_scoped_tool_exists_and_only_previewed_scopes_write`
holds any future write scope to the same rule.

## Labs pages (and the sandbox)

A page (`kind: "page"`, WORKFLOW_REFERENCE.md §16) that sets `config.agent.share` gets the
panel on every route that draws it -- its own address (`labs:page_home`, `labs:page`), its
workflow URL, a supply tab -- whether its code runs in the sandbox frame or not. Not in
edit or present mode, and not for a workflow a route draws in page mode (`page_context`
reads only pages).

- **Scope: `page:read` alone, wherever it is drawn.** On the run URL, whose own
  registration grants `workflow:read` + `workflow:act`, the page token is signed again for
  `labs:workflow:run:page` (`views._page_only_panel`). The one tool is `page_context`:
  the page's name, scope, the Settings it reads and its workflow sources' runs, as the
  visitor; it refuses a non-page or a non-sharing page whatever the token says.
- **Page state:** `resource: labs-page://<scope type>/<scope key>/<slug>`,
  `backing_tool: page_context`, `filters: {definition_id, scope_type, scope_key, <owner>}`
  -- exactly `page_context`'s arguments. `view.shareSelection` narrows `visible_ids` and
  sets `filters.drilled`, as on a run page (`components/workflow/agentSelection.ts`).
- **Sandboxed**, the panel is host chrome, outside the frame; the frame's
  `shareSelection` is a bridge request the host rebuilds (`host.ts sanitizeSelection`:
  strings and primitives only) and hands to `canopyHost.updatePageState`, so page code
  cannot touch the resource, the tool, the server's filters or the path. The host page's
  enforced `frame-src` names the widget's `<canopy>/embed/chat`; the site's report-only
  policy names `<canopy>/embed/widget.js` (script) and the same frame.
- **Unsandboxed** (the switch off), page code shares the document with the panel and can
  call the widget directly, as an unsandboxed workflow's code can on its run page: it can
  steer the agent's view, never its scopes, which the server decided.

`connect_labs/mcp/tests/test_canopy_panel_end_to_end.py` walks it for a page (two routes,
sandboxed and not) and a workflow run: rendered page state, the panel's mint, the
ID-JAG's scopes, the redeem at `/o/token/`, and the MCP call as the visitor.

## Contact details and transcripts

`marketplace_orgs_get` returns real people's names and email addresses when asked
— that is what drafting outreach needs, and the same fields are already on
`/labs/marketplace/org/<slug>/` for every labs user. Two rules:

- `include_contacts` is opt-in per call, so "what is in this round" does not drag
  PII through a transcript with no use for it.
- Nothing here should grow a field the org page does not already show. A
  transcript persists.

## Setting it up on a deployment

Labs Admin › Canopy agents (`/labs/admin/canopy-agents/`) is the one place labs' side is
set up. Besides it, a deployment needs one secret and, in canopy, the site.

**1. Register the site in canopy**, as a workspace owner, at canopy › Connected sites
(`https://canopy.dimagi.com/w/<workspace>/settings/connected-apps`): type labs' address
(`https://labs.connect.dimagi.com`), press **Fill from the site**, check what it filled
in, and confirm. canopy reads labs' signed site document at
`/.well-known/canopy-site.json` (also at `/labs/canopy/site.json`): its name
(`connect-labs`, `CANOPY_APP_NAME`), its origins (`LABS_PUBLIC_URL`), where its keys are
(`/labs/canopy/jwks/`), and, while the grant is on, its issuer and MCP server. Then
choose the agents canopy may offer on labs; they are what labs' settings page chooses
from. (A local labs at `http://localhost:8000` is added as another site URL by hand.)

**2. Generate a key pair** and put the private half in Secrets Manager as
`CANOPY_SIGNING_KEY` (already referenced by `deploy/task-definitions/*.json`). It
never leaves labs' server:

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

Newlines survive an env var as literal `\n`; the settings module unescapes them.

**3. Choose the agents** at Labs Admin › Canopy agents, as Dimagi staff: tick the agents
labs shows, which may work with sensitive data, the default agent, the runner flags, and whether
canopy may act as the visitor; then Save. Until then no agent is offered.

The page also shows the **canopy workspace** labs' site is registered in. Labs learns it
from canopy's first answer (and records it on every save) and sends it as
`CANOPY_HOST["WORKSPACE"]`, so deleting or moving the default agent cannot break
canopy's list; until it is known the default agent names the workspace. If canopy moves
the site and refuses the stored one (`workspace_mismatch`), the page offers to ask by
the default agent again. When canopy says nothing, the page shows canopy's own reason
(the SDK's `last_error()`: `unreachable`, `unknown_issuer`, `workspace_mismatch`, …), and
canopy's problem sentences link to the canopy page that fixes each. Restoring an earlier
change is checked against canopy's list like a save, and refused when canopy no longer
lists its default agent.

The only environment variable is the secret, `CANOPY_SIGNING_KEY`. `CANOPY_BASE_URL` defaults to
`https://canopy.dimagi.com` on AWS (`config/settings/labs_aws.py`) and `CANOPY_APP_NAME`
to `connect-labs`; set either only to point labs at another canopy or site name (locally,
`CANOPY_BASE_URL` has no default, so the panel is off). canopy's OAuth client id is
derived from `CANOPY_BASE_URL` (`<base>/oauth/client.json`). The tenant on every token is
the saved default agent's.

The panel does not render at all unless the signing key, `CANOPY_BASE_URL` and
`CANOPY_APP_NAME` are all set. That is deliberate: a launcher that opens onto an error the
page cannot explain is worse than no launcher.

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

**To turn it on**, tick "Canopy may act as the visitor at labs' MCP" at Labs Admin ›
Canopy agents and save (it also needs `LABS_PUBLIC_URL`, which every JWT here names as
issuer, and the signing key). canopy's client id is then derived from `CANOPY_BASE_URL`
(`https://canopy.dimagi.com/oauth/client.json`). The MCP's DPoP gate reads the switch
once per request, off the event loop (`mcp.server.dpop_gate`).

Off, nothing else changes: no ID-JAG is issued, `/o/token/` answers the grant with
`unsupported_grant_type`, the metadata does not advertise it, and delegated tokens issued
while it was on are refused.

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

|              |                                                                                                                                                                                                                                                                                                                 |
| ------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Principal    | `canopy:probe` — a service account, never a person. Created by `mcp/migrations/0006` (the deploy runs `migrate` whenever a migration changes). Active, no usable password, no staff bit, no email, no PAT; the `:` is a character no Connect username can have, and the OAuth callback refuses the name besides |
| Scope / tool | `marketplace:read` → `marketplace_rounds_list` with `{"open_only": true}`: no per-user gate, so it succeeds for the probe user; an empty list is still a success                                                                                                                                                |
| Denied tool  | `list_templates` — a real read tool outside the scope                                                                                                                                                                                                                                                           |
| Page         | `marketplace:network` (audit only)                                                                                                                                                                                                                                                                              |
| Endpoint     | `{LABS_PUBLIC_URL}/labs/canopy/probe/` — must be the public URL exactly (a DPoP proof's `htu`), and is advertised as `canopy_probe_endpoint` in `/.well-known/oauth-authorization-server`                                                                                                                       |

All of it is in `connect_labs/labs/canopy.py` (`PROBE_*`, `probe_subject`), and
none of it is a secret. It is **on wherever the grant is** and needs no env var
of its own: with the grant off the endpoint 404s, and it 404s too until
the probe user exists. MCP calls made with a probe token land in `MCPAuditLog` as
`canopy:probe`, with canopy's client id. To stop it, deactivate the user (canopy
then reports a refused probe rather than an unconfigured one).

## When it does not work

| Symptom                                           | Cause                                                                                                                                                    |
| ------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| No launcher at all                                | nothing is saved at Labs Admin › Canopy agents, or the signing key is unset — the include renders nothing                                                |
| Every mint 401s with `bad_signature`              | canopy cannot reach `/labs/canopy/jwks/`, or it is serving `{"keys": []}` because `CANOPY_SIGNING_KEY` is not a readable PEM                             |
| Launcher, then a token error                      | the mint 403'd (CSRF binding), 502'd (canopy unreachable) or 401'd (signature). Labs logs canopy's own words; the browser deliberately does not get them |
| Blank panel, console says a frame refused to load | labs' origin is not in the site's **Site URLs**                                                                                                          |
| "No agent is available here yet"                  | no allowed-agents row, or the agent is not in that workspace                                                                                             |
| The agent knows nothing about the page            | `resource` not declared, or the panel rendered in `<head>`                                                                                               |
| A message sends, no reply ever arrives            | no runner online for that agent — canopy-side, not labs                                                                                                  |

## Related

- `connect_labs/marketplace/README` territory: the directory is the master org
  registry, fed from the LLO Directory sheet by `marketplace_import`. These tools
  are read-only because that sheet is the source of truth.
- canopy-web#922 (the CSRF option), #925 (page state for contacts), #928 (the
  assurance grade an embedded site cannot earn).
