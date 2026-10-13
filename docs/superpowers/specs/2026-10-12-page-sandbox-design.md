# Page code runs in a sandbox

Status: approved for build (Jonathan, 2026-10-12: "do it, make sure it works for pages
with the canopy SDK, then we apply it to workflows"). Pages first; workflows are phase 2
and are designed here only so phase 1 does not paint them into a corner.

## The problem

A page (a workflow definition with `kind: "page"`, `workflow/page_mode.py`) is drawn by
`DynamicWorkflow.tsx`: its JSX is Babel-transpiled in the browser and `eval`'d **on labs'
own origin, inside the viewer's session**. Every read the runner makes is checked against
the viewer, live, so a page never *shows* a viewer something they may not see. Nothing
stops the page's *author* using the viewer's access:

- The code can read the CSRF token from the DOM and call any labs endpoint as the viewer.
  Six of the endpoints `actions` reaches are `@csrf_exempt` besides.
- `POST /labs/mcp/tokens/create/` is guarded by login + CSRF only and returns the raw PAT
  in its HTML. So page code can mint a 90-day key with the viewer's full access and send
  it anywhere: CSP is report-only and `connect-src` allows any `https:` host.

An organisation's landing page is opened by everyone in the organisation, admins included,
often without choosing to. Today that is safe only because the demo organisation is the
only one with a landing page and only staff can set one.

## The fix, in one paragraph

Page code runs in an `<iframe sandbox="allow-scripts …">` **without** `allow-same-origin`,
so its origin is opaque: it has no labs cookies, cannot read the host page, and its
network requests are cross-site (the `SameSite=Lax` session is not sent). The frame
document is served by labs with an **enforced** CSP that allows no `connect-src` to labs
and no arbitrary hosts. The trusted labs page around it (the *host*) holds the data,
passes it in, and answers a short allowlist of requests over a private `MessageChannel`,
as the viewer, with the access checks it already makes. The canopy agent panel stays on
the host; the page narrows what the agent sees through the bridge.

The page keeps everything Jonathan wants from it: arbitrary dynamic code, the viewer's
access applied live. It loses only what it never declared.

## Why an opaque origin and not a sandbox subdomain

A subdomain (`sandbox.labs…`) needs DNS, an ALB certificate and listener rules, which are
click-ops today, plus `ALLOWED_HOSTS` and session-cookie domain care. An opaque origin
needs none of that and is *stronger*: there is no origin to hold cookies or storage at
all. The cost is that `localStorage`/`sessionStorage` throw in an opaque origin, which the
frame runtime shims in memory (no page uses them today; one deprecated workflow does).

## Components

### 1. The frame document: `GET /labs/sandbox/render/`

A labs view returning a fixed, data-free HTML shell. It does not extend `base.html`, never
renders a CSRF token, user data or analytics, and is identical for every viewer (so it is
cacheable). Response headers:

- `Content-Security-Policy` (**enforced**, not report-only):
  - `sandbox allow-scripts` — so even opened top-level the document is opaque-origin.
    (`allow-modals`, `allow-downloads`, `allow-popups`, `allow-forms`,
    `allow-top-navigation*`, `allow-same-origin` are deliberately absent; confirm and
    download are bridge methods the host draws.)
  - `default-src 'none'`
  - `script-src` the labs static prefix, the pinned Babel URL, `https://api.mapbox.com`,
    the pinned Chart.js / Leaflet URLs, `'unsafe-eval'` (Babel output) and
    `'unsafe-inline'`. Named by full URL, never `'self'` (in an opaque document `'self'`
    is not a guarantee worth relying on).
  - `style-src` the static prefix, `https://api.mapbox.com`, `https://unpkg.com/leaflet@1.9.4/`,
    Google Fonts CSS, `'unsafe-inline'`; `font-src` static + `https://fonts.gstatic.com`.
  - `img-src data: blob:` + the Mapbox **paths** GL needs; `connect-src` the same paths
    (`api.mapbox.com/styles/v1/mapbox/`, `/fonts/v1/mapbox/`, `/v4/`, `*.tiles.mapbox.com`,
    `events.mapbox.com`) — never whole `api.mapbox.com`, whose datasets/uploads APIs are a
    two-way channel with an attacker's own token; `worker-src blob:`. **No labs origin and no wildcard**: page code cannot reach labs except
    through the bridge, and cannot send data to an arbitrary host.
  - `frame-src 'none'; form-action 'none'; base-uri 'none'; object-src 'none'`.
  - `frame-ancestors` the labs public origin.
- `X-Frame-Options` exempted for this view only (`frame-ancestors` above replaces it).
- `Cross-Origin-Resource-Policy: same-origin`, `Referrer-Policy: strict-origin` (a
  URL-restricted Mapbox token needs the labs origin as referrer).
- No login, no redirect, query string ignored: `frame-src` path matching is dropped after
  a redirect, so the shell URL must answer 200 for every suffix and query (tested).

The CSP is built in one function (`workflow/sandbox.py`) from settings
(`LABS_PUBLIC_URL`, `STATIC_URL`) and pinned CDN URLs, and tested as a string.

### 2. The host page

`page.html` (the `/labs/p/…` view and the edit-mode preview of a `kind: "page"` definition)
stops loading the runner's eval path and instead renders:

```html
<iframe id="page-sandbox" src="/labs/sandbox/render/"
        sandbox="allow-scripts allow-modals allow-downloads"
        referrerpolicy="strict-origin"></iframe>
```

and the runner bundle in **host mode**, which does everything it does today *except*
transpile or run render code: it reads `#workflow-data`, gets pipeline / supply /
workflow-source data as today (as the viewer), and drives the frame.

The page response also carries a second, **enforced** CSP header containing only
`frame-src <labs>/labs/sandbox/render/ <CANOPY_BASE_URL>/embed/chat` (the widget's exact
path — a whole canopy origin would let the frame navigate onto any canopy-hosted HTML,
which keeps the opaque origin and so would also pass the bridge handshake). This is what stops the frame
navigating *itself* to `https://evil/?<data>` (frame navigations are checked against the
embedder's `frame-src`). The site-wide report-only policy is unchanged.

### 3. The bridge

1. The frame runtime loads and posts `{type: "labs-sandbox:hello"}` to `parent`.
2. The host attaches its listener before setting `src`, and accepts a hello only if
   `event.source === iframe.contentWindow` and `event.origin === "null"`, **once per
   frame load**. It creates a `MessageChannel` and transfers one port with `init`. From
   then on everything rides the port; `window` messages are ignored. A second `load` of
   the iframe (the frame navigated) closes the port and the page is not redrawn there:
   the host shows "this page tried to leave its frame" and stops. The frame resends hello
   until `init` arrives.
3. `init` carries: `renderCode`, the page props as plain data (`definition`, `instance`,
   `workers`, `pipelines`, `supply`, `workflows`, `scope`, `config`, link bases,
   `view` data, `asOf`, server `today`), `mapboxToken`, and the static/CDN URLs to load.
   `update` follows whenever the host's data changes (the pipeline stream finishing,
   a query result the host chose to fold in).
4. Frame → host requests, `{id, method, params}` → `{id, result}` / `{id, error}`.
   The host answers only these:

| Method | What the host does | Phase |
|---|---|---|
| `querySupply`, `queryWorkflow` | calls the existing endpoint for **this page's definition** (the frame cannot name another; the host strips any definition/opportunity/program ids from params), as the viewer; size- and rate-limited | 1 |
| `navigate {url, newTab}` | only with user activation (`navigator.userActivation.isActive` on the host when the message arrives); parsed as `new URL(url, location.origin)`, required `origin === LABS_ORIGIN` and `https:`/same scheme, `pathname` on an allowlist (`/labs/p/`, `/labs/workflow/<id>/run/`, `/supply/`, `/labs/settings/`, `/labs/targeting/`, `/labs/marketplace/`, `/microplans/`), never `/o/`, `/accounts/`, `/labs/mcp/`, `/admin/`; the host navigates to `u.href`, new tabs with `noopener` | 1 |
| `shareSelection {visible_ids, drilled}` | the host builds `{visible_ids, drilled}` itself (strings, count and length capped) and calls `window.canopyHost.updatePageState` — the frame can never touch `filters`, `path`, `resource` or `backing_tool` | 1 |
| `resize {height}` | sets the iframe's height (clamped) | 1 |
| `copyText {text}` | user activation required; `navigator.clipboard.writeText` on the host (size-capped) | 1 |
| `confirm {text}` | host-drawn dialog, visibly labelled as the page's content | 1 |
| `download {filename, mime, bytes}` | user activation required; extension allowlist (csv, xlsx, json, png, pdf), size cap | 1 |
| `reportError {message}` | shows the page's render error in host chrome, inserted as text, never HTML | 1 |
| `queryPipelineRows` | **not on pages in phase 1**: pages get no visit-level pipelines (see residual risk) | 2 |
| `runAction`, `startJob`, `createTask`, image fetch, job status | phase 2 (workflows), each mapped to an existing endpoint with a host-side preview/confirm for writes | 2 |

   Unknown methods get an error. Params are validated (types, sizes) before use.

### 4. The frame runtime: a new webpack entry `workflow-sandbox`

Bundles React, ReactDOM, `window.LabsReport` and the transpile-and-mount code from
`DynamicWorkflow.tsx` (extracted to a shared module so the run page and the frame compile
render code identically). It loads `connect_map.js`, Mapbox GL, Leaflet and Chart.js as
`<script>`s, and Tailwind CSS (+ Font Awesome, which it imports) as a stylesheet — so
render code sees the same globals and classes it does today. It builds the same props
render code receives today, with `actions` / `view` functions that are bridge calls
(phase-1 methods; phase-2 ones reject with "not available on a page yet").

It also: intercepts clicks on `<a>` (capture phase) and turns them into `navigate`
(honouring `target="_blank"`); installs in-memory `localStorage` / `sessionStorage`;
reports its content height with a `ResizeObserver`; catches render errors and reports them.

### 5. The canopy agent panel on pages

Pages get the panel, gated exactly like run pages: only when the definition shares
(`config.agent.share`, `agent_sharing.py`).

- `panel_context(request, resource="labs-page://<scope kind>/<scope key>/<slug>",
  backing_tool="page_context", visible_ids=[], filters={definition_id, scope kind, scope id},
  path=request.path)`, included in `inline_javascript` (the documented trap).
- `canopy.PAGE_SCOPES` registers the page routes with a new `page:read` scope →
  `page_context`.
- `page_context` (new MCP tool, read-only): given the definition and scope, returns what
  the page is built from **as the visitor** — name, scope (names, what's under it), its
  `config_reads` values and its `workflow_sources` runs summary — reusing the same
  functions the page view uses. It refuses a definition that is not `kind: "page"`, or that
  does not share, whatever the token carries (the same rule the `workflow_*` run tools follow).
- Page code narrows the view with `view.shareSelection(...)` as run pages do; the bridge
  carries it to `canopyHost.updatePageState`, which keeps `resource` and `backing_tool`
  fixed. The page cannot change what the agent may do, only what it looks at.

## What a page author notices

- Plain `<a href>` links work (same tab or `target="_blank"`), on a click, to labs pages.
  A link to a workflow still opens that workflow unsandboxed until phase 2; that is no
  worse than any link to it anywhere, but it means phase 1 protects what a page *runs*, not
  where a click on it leads.
- `alert`/`confirm`/`prompt` are gone; `view.confirm` and `actions.download` replace them.
- `window.location = …`, `fetch(…)`, cookies, `document.cookie`, the CSRF token and the
  host page's DOM are gone. `page_blank` uses none of them; live page records are scanned
  before the switch (`workflow_list` + `workflow_get` over every `kind: "page"`).
- The workflow authoring guide (`workflow_authoring_guide`, WORKFLOW_REFERENCE.md §16)
  says what a page may and may not do.

## Rollout

- Pages are sandboxed unconditionally once this ships; there is no per-page opt-out. A
  setting `LABS_PAGE_SANDBOX` (default on) exists only as an emergency switch back.
- The edit-mode preview of a page is sandboxed too: staff editing someone else's page
  must not run that author's code as staff.
- Item 5 of the parked list (who may edit pages) can widen after this ships.

## Every route that draws a page

The decision is made once, in `WorkflowRunView.get_context_data`, from the **consuming**
definition's `kind` (whatever its `render_source`, since a template workflow's render is
just as author-controlled): a page is sandboxed on every route that draws it —
`/labs/p/…`, `/labs/workflow/<id>/run/` with no run, edit mode, present mode and a page
pinned as a supply tab (`supply_chain/workflow_views`). If the definition cannot be read,
nothing is drawn (fail closed; `_is_page_definition` no longer returns False on error).
In host mode the runner loads no Babel and evaluates nothing. A test walks every route.

## A server-side guard, independent of the browser

Cookie rules for an opaque initiator differ between browsers (WebKit computes the site for
cookies from the top document, so Safari may send the Lax session cookie), so the session
is not left to them. A middleware refuses any session-authenticated request carrying
`Origin: null` (except the shell itself), and any unsafe-method session request whose
`Sec-Fetch-Site` is `cross-site`. The `@csrf_exempt` endpoints the runner reaches get an
`Origin` check. Bearer/DPoP/PAT requests are unaffected.

## Residual risk, stated

**The invariant is: the session cannot leave the frame; data given to the frame can.**
WebRTC (a peer connection to an attacker's TURN server) is a full data channel that no
shipping browser lets CSP close, and removing `RTCPeerConnection` in the frame is undone by
a fresh `about:blank` child. So whatever the host hands the frame should be treated as
readable by the page's author. Phase 1 therefore gave pages no visit-level pipelines and no
`queryPipelineRows` (withdrawn in phase 2: pages are as definable as workflows, and carry the
same residual risk; see "Pages become as definable as workflows"). Phase 2 has to decide what workflows may read with
that in mind (e.g. limiting a definition's sources to what its author can also read).


- **UI spoofing inside the frame** (a page drawing a fake login box). The frame cannot
  submit forms or open popups, and cannot reach labs, so it has nowhere to send what it
  collects except over the side channels above.

## Tests

- Python: the frame view's headers (enforced CSP string, `sandbox` directive present, no
  labs origin in `connect-src`, XFO exemption) and body (no CSRF token, no user data);
  the page response's enforced `frame-src`; `page.html` renders the iframe and not the
  eval path; edit-mode preview of a page is sandboxed; `PAGE_SCOPES` covers page routes and
  maps to an existing tool; `page_context` refuses non-pages and non-sharing pages and
  reads as the visitor.
- JS: the host's bridge refuses unknown methods, non-labs `navigate`, and a `hello` from
  any source other than the iframe; the frame runtime turns anchor clicks into `navigate`.
- Every route that draws a page renders the iframe (walked in one test); the shell URL
  answers 200 without login or redirect for any suffix and query; the guard middleware
  refuses `Origin: null`; `navigate` refuses `//evil.com`, `/\\evil.com`, `\t//evil`,
  `javascript:…`, `https://<labs>.evil.com`, `https://<labs>@evil.com`, `/o/authorize/…`
  and any navigation without user activation.
- Live, in a browser (gstack browse, Chromium; Firefox and Safari by hand before a real
  organisation gets a landing page) against a local server and then labs: a test page
  whose code tries `fetch('/labs/mcp/tokens/create/')`, `document.cookie`,
  `parent.document`, `localStorage`, `location = 'https://example.com'` and an
  `<img src="https://example.com/x">` — every one must fail — and the demo organisation's
  landing page must still draw, link, and narrow the canopy panel.

## As built (phase 1), and where it differs from the above

Built as written, with these differences, each for the reason given:

- **The iframe is created by the host bundle, not written into `page.html`.** Step 3.2
  needs the listener attached before `src` is set and every `load` counted from the
  first; a static `<iframe src>` can load before a deferred bundle runs. One component
  (`components/workflow/sandbox/SandboxFrame.tsx`) draws it on every route that draws a
  page (page address, workflow URL, edit, present, supply tab), so `page.html`,
  `run.html` and `supply_chain/workflow_view.html` all get it the same way.
- **The iframe's `sandbox` attribute is `allow-scripts` only.** The frame document's CSP
  `sandbox allow-scripts` already strips `allow-modals` and `allow-downloads` (the
  effective set is the intersection), and confirm and download are bridge methods, so
  naming them on the iframe changed nothing but invited a reader to think they apply.
- **`script-src` names the Mapbox GL file, not the whole of `https://api.mapbox.com`**
  (and `style-src` its CSS path). Tighter, same function.
- **The shell names its libraries itself.** The frame document loads Babel, Chart.js,
  Leaflet, Mapbox GL, `connect_map.js` and the sandbox bundle as fixed `<script>`s, so
  `init` does not carry URLs to load. The shell stays identical for every viewer.
- **`navigate` also allows `/labs/workflow/` (the workflow list).** `page_blank` links
  every opportunity card to it; without it the starter page's links would be refused.
- **The panel's `filters` carry the page's owner** (`program_id`, `opportunity_id` or
  `organization_id`) beside `definition_id`, scope kind and key: `page_context` needs it
  to read the definition where it lives. `page:read` is registered on the page routes and
  `supply_chain:workflow_view`; on `labs:workflow:run` (whose own scopes are a workflow's)
  a page's panel token is signed for `labs:workflow:run:page`, which grants `page:read`
  alone (review fix, below).
- **`page_context` is not in `RESTRICTED_TOOLS`** (it is in `USERVISIT_DATA_TOOLS`): a
  workflow source's run summary can carry indicator values computed from visits.
- **The shared selection's `drilled` lands under `filters.drilled`**, the server's
  filters kept as they were -- exactly what a run page sends. The frame never sets
  `filters`, `path`, `resource` or `backing_tool`.
- **Pages get no workers either**, not just no pipelines (worker names are personal
  data; the residual-risk rule). `workers` is `[]` and the pipeline and worker endpoints
  are removed from the page's data, not merely unused.
- **Fail closed, concretely:** `_is_page_definition` returns `None` ("could not tell"):
  the bare workflow URL then renders instead of redirecting to the list, and a supply tab
  starts no run; the render reads the definition itself and decides.
- **The guard middleware sits straight after `AuthenticationMiddleware` in every settings
  module** (local, test and labs_aws anchor their labs middleware after it), so nothing
  session-dependent runs for a request it refuses.
- **The site-wide report-only CSP is not added to the shell** (its `frame-ancestors
  'none'` would only be noise on a document meant to be framed); on the page itself both
  apply, joined into one header if the site policy is ever enforced.
- **`localStorage` in the live test.** The frame shims storage in memory (component 4),
  so "must fail" is checked as "the page cannot see labs' storage": a key the host page
  set is absent in the frame.
- **The unsandboxed runner gains `view.confirm`, `actions.download` and
  `actions.copyText` too**, so a page author's code works the same in both places.
- The webpack entry is in `webpack/base.config.js` (there is no `webpack.config.js`).

**Review fixes (2026-10-12, after #2449).** An independent review found these; each has
a test that fails without it (`workflow/tests/test_page_sandbox.py`,
`scope_config/tests/test_review_fixes.py`, `components/workflow/sandbox/sandbox.test.js`):

- **What a route draws in page mode is sandboxed, whatever the definition says.**
  `should_sandbox(definition, page_mode=...)`: a scope's home from Settings no longer
  decides by the definition's `kind` alone. A home that is not a page is refused twice:
  when it is set (`labs` namespace `check_targets`, read as the person setting it) and
  when it is opened (`PageView` says the home is not a page and draws nothing of it).
- **A page is a definition with `kind: "page"` or one made from `page_blank`**
  (`page_mode.is_page`), so pages made before `kind` was written are pages everywhere it
  is decided (sandbox, home check, run list, `workflow_list`). Chosen over a backfill:
  it fails toward the sandbox and needs no write to existing records.
- **A page never reads a run.** A `run_id` on a page's address is ignored; on every
  route, a run is drawn only by the definition it belongs to; and the frame's
  `instance` never carries `snapshot` (nor does supply come from one).
- **The origin is the request's own** (`labs_origin`), for the host's `frame-src`, the
  shell's `frame-ancestors` and the navigate check alike, so another allowed host draws.
- **The guard decides by the session cookie**, not by whether an `Authorization` header
  rides beside it.
- **`navigate` refuses `sync` and `template` query parameters**, and the run page's
  `?sync=true` GET (which overwrote render code) is removed; syncing is the POST
  `sync-template` endpoint and the MCP tools.
- **Relative links in a page resolve against the host page's URL** (`pageUrl` in INIT),
  not the shell's, so they navigate as they did before the frame.
- The old card pages' `pages-cards` entry, which nothing loaded, is deleted.

Verified in Chromium (gstack browse) against a local server: every attempt on the live
test list failed (fetch to labs and to another host, `document.cookie`, `parent.document`,
labs' `localStorage`, an off-site `<img>`, `window.open`, `alert`, `location = ...`, which
the host caught as "this page tried to leave its frame"), and `page_blank` drew, its links
navigated on a click, the frame resized, `view.confirm` drew the host dialog, and
`shareSelection` reached `canopyHost` with the injected `resource` dropped. Firefox and
Safari by hand, and the same on labs after a deploy, remain to do before a real
organisation gets a landing page.

## Phase 2: workflows (as built, 2026-10-12)

Jonathan, 2026-10-12: "fix everything that's still open", after phase 1. Phase 2 puts a
workflow's own render code in the same frame, on every route that draws it (run page, edit
mode, `?present=1`, a supply tab, a template draft preview -- they are all
`WorkflowRunView`, so the decision is still made once, in `should_sandbox`).

### The design question, and the ruling

**A workflow legitimately receives visit-level data (workers, pipelines, its run and
snapshot). Inside the frame that data can leave over WebRTC. Does sandboxing a workflow
still help?** Ruled yes, and it is not close. The invariant from phase 1 holds unchanged --
*the session cannot leave the frame; data given to the frame can* -- and today a workflow's
code runs on labs' origin WITH the session, which is strictly worse: it can already read
everything its viewer can and also act as them. In the frame it keeps only what the host
hands it (the data its render reads today) and loses the session: it cannot mint a token,
write a record outside its run, run a declared action without the host's own dialog, or call
any labs endpoint the route table does not name. So workflows get the same data as before
(no new exposure) and lose the ability to act as the viewer (the exposure that matters).
Cost: nothing a viewer sees changes, except the questions below.

### Pages become as definable as workflows (ruling, 2026-10-12)

Jonathan, during phase 2: **"pages should be completely definable like workflows are."**
So the phase-1 page-only restrictions (no pipelines, no workers, no `queryPipelineRows`, no
tasks / audits / images / boundaries / tiles) are gone: **one capability set, one code path,
gated only by "has runs or not".** A page is drawn by the same frame component and the same
host handlers as a workflow; it is handed its `pipeline_sources` (streamed as on a workflow),
`workers`, `supply`, `workflows`, `scope` and `config`; its code reaches labs through the same
route table with the same questions.

What only a run has, and so a page cannot: `updateState` to a run (a page's state stays in the
frame), `view.complete`, `actions.runAction` (a declared action's roster, confirm token and the
tasks it attaches are all a run's: `workflow/actions.py` takes a run), background jobs (they
write their results to a run), and every `/labs/workflow/api/run/<id>/...` write -- refused by
routes.ts because a page has no run id to bind. The dispatcher refuses the run-only methods
for a page with "needs a run, and a page has none"; a parity test pins that every other method
answers a page exactly as it answers a workflow.

Cost: a page's author can now see (and, over WebRTC, take) the visit-level data its pipelines
read, exactly as a workflow's author can. That is the same residual risk, stated once for both
below; the phase-1 rule "pages get no visit-level data" is withdrawn by this ruling. Pages keep
what makes them pages: no run is ever read (a `run_id` on a page's address is ignored, no
snapshot reaches the frame). Declared actions on a page would need the action framework to act
on a scope instead of a run: a follow-up, not built here.

### What a workflow's code can make labs do: one table

Every labs request a workflow's code makes is a bridge `fetch` the host answers only if it
matches a route in `components/workflow/sandbox/routes.ts`. That file is the whole of what
workflow code can make labs do as the viewer; review it, not 21 templates.

| Gate | What | Host does | Ruling |
|---|---|---|---|
| `read` | ~30 read endpoints the seed templates use (run history, snapshot preview, indicator definitions, audit sessions, AI agents, image questions, visit images, benchmarks, boundaries, job status...) | makes it, as the viewer | A route that names an opportunity, a program, this workflow's pipelines (`pipeline-rows`, `pipeline-query`, `case-states`, `supply-*`, `workflow-*`) or this run must name one this workflow covers. Reads of ANOTHER workflow's runs, history or indicator definitions are not id-bound: the companion reports (KMC, indicator cascade) read their program report that way. Cost recorded under residual risk. |
| `own` | writes to this run's or this definition's own record: state, flags, worker result, rename, reconcile, audit-batch config, job cancel, and a job whose type only computes into this run (`audit_par_rollup`, `muac_plausibility_compute`, `program_admin_rollup`, read from the body the host is about to send) | makes it, no question | Bound to this run and definition (in edit mode, run 0, they are refused). Render code makes some from effects; a question on every state save would make every workflow unusable. |
| `effect` | tasks, task updates, coaching conversations with a worker (`/tasks/<id>/ai/initiate/`), audits (create, delete, cancel), any other job (they create audits across opportunities), completing or deleting the run, the synthetic demo helpers | asks first, in labs' own words ("This workflow wants to create tasks, as you."); a yes holds for that kind of write for 2 minutes | One click that creates a batch is one question. The words are labs', never the page's. |
| -- | everything else: `/labs/mcp/`, `/o/`, `/accounts/`, `/admin/`, render code, definitions, sharing, deleting a workflow, pipeline schemas, **and a workflow's declared actions** | refused, logged as `[sandbox] refused fetch: ...` on the host page | Declared actions run only through `actions.runAction`, which opens the host's own ActionDialog (preview, confirm token, execution), exactly as on the run page. |

Writes never follow a redirect (a 307 would repeat the write elsewhere); reads follow labs'
own (the context middleware adds the session's opportunity to a bare URL) and where they
land is checked against the table again. Request headers are cut to `content-type` and
`accept`; the host adds its own CSRF token.

### Every prop and method, and its bridge equivalent

| Render code uses | In the frame |
|---|---|
| `definition`, `instance` (run, state, snapshot), `workers`, `pipelines`, `supply`, `workflows`, `scope`, `config`, `links` | passed as data (INIT); later changes as partial UPDATEs (only the keys that changed: pipelines can be tens of MB) |
| `onUpdateState(s)` | bridge `updateState` -> the host's `handleUpdateState` (this run's state endpoint; local in edit mode); the run's state comes back as an UPDATE |
| `view.*` (`workers`, `pipelines`, `state`, `isCompleted`, `asOf`, flags/audits/tasks + `*For`, `ensureAutoFlags`, `workflowActions`, `shareSelection`, `confirm`) | built by the same function as on the run page (`components/workflow/runView.ts`); `ensureAutoFlags` posts through the frame's fetch (an `own` route) |
| `view.complete({confirm})` | bridge `completeRun`: the host asks (its words, the page's text quoted under them), completes, reloads |
| `actions.createTask`, `createTaskWithOCS`, `initiateOCSSession`, `startJob`, `cancelJob`, `deleteRun`, `streamJobProgress`, `createAudit`, `getAuditStatus`, `streamAuditProgress`, `cancelAudit`, `saveWorkerResult`, `completeRun`, `getTaskDetail`, `getAITranscript`, `getAISessions`, `updateTask`, `saveAITranscript`, `checkOCSStatus`, `listOCSBots` | the run page's own implementations (moved to `components/workflow/actionHandlers.ts`), whose `fetch` in the frame is the bridge's: each lands on a route above |
| `actions.runAction(key, args)` | bridge `runAction`: key must be one this workflow declares; the host opens ActionDialog |
| `actions.queryPipelineRows`, `querySupply`, `queryWorkflow` | bridge methods; the host calls this definition's endpoints with its own scope params |
| `actions.download`, `copyText`, `openTaskCreator` | bridge `download` / `copyText` (on a click) / `navigate` to `/tasks/new/` |
| `actions.navigate(url, {newTab})`, `actions.reload()` | new (on the run page too): bridge `navigate`. Workflows may also open `/audit/`, `/tasks/`, workflow and run pages, an indicator sheet, a visit photo and the "Authorize" sign-in starts; never an API address. Without a click just before, the host asks, showing the path |
| `fetch(<labs path>)` (76 calls in 21 templates), `LabsAudit.fetchSessions`, `ConnectMap` boundaries | the frame's `fetch` is the bridge's for labs paths (route table); other hosts get the browser's, which the CSP mostly refuses |
| `<img src="<labs path>">` (visit photos) | fetched by the host, shown as `blob:` URLs (the frame's img-src allows no labs address) |
| `EventSource` | absent: the templates that prefer a stream fall back to their plain fetch |
| `window.location = ...`, `location.reload()` | the frame navigates, the host's `frame-src` refuses where it goes and the host stops the frame ("tried to leave its frame"). It cannot be shimmed: the Navigation API that could cancel it is disabled in an opaque-origin document (checked in Chromium). The three seed templates that did this now call `actions.navigate` / `actions.reload`; a live COPY that still does is why the rollout scans copies before `all` |
| `window.confirm` | returns false in the frame (no `allow-modals`); the one template that used it now awaits `view.confirm` |
| `alert`, `window.open`, `history.pushState/replaceState`, `navigator.clipboard`, `<a download href="blob:">`, `document.cookie`, the DOM CSRF token, `#user-opportunities`, `WORKFLOW_API_ENDPOINTS`, `localStorage` | host notice; `navigate` in a new tab; the host's query/hash (same path); `copyText`; `download`; `""`; a dummy (the host adds the real token); this workflow's opportunities only; passed in; in memory |
| `window.LabsReport`, `ConnectMap`, `PlanLayers`, `MicroplansMapPanel`, `LabsAudit`, Chart.js, Leaflet, Mapbox GL | loaded by the shell from labs static and the pinned CDN URLs; Leaflet tiles from OpenStreetMap and CARTO are now in img-src (GETs to tile servers nobody here controls) |

### Rollout

A big-bang switch would break live dashboards whose render code (copies, not followers)
nobody has checked. So `LABS_WORKFLOW_SANDBOX` (default `opt_in`):

- **Followers of a checked template** are sandboxed: `TEMPLATE["sandboxed"] = True` and
  `render_source: {"template": key}`. Every checked template's `DEFINITION` also carries
  `config.sandbox: true`, so every NEW instance is sandboxed even after it stops following.
- **A copy** (own render code) is sandboxed when its definition says `config.sandbox: true`.
- **`?sandbox=1`** sandboxes any workflow for that view -- it can only turn the sandbox on, so
  a link cannot take a workflow out of it. Staff see a line under the workflow saying which it
  is, with a "View it sandboxed" link.
- **No per-workflow opt-out** (review fix): `config.sandbox: false` means nothing, so an author
  cannot take a workflow that follows a checked template out of the frame.
  `LABS_WORKFLOW_SANDBOX=off` and `LABS_PAGE_SANDBOX=false` are the emergency switches, for
  everything at once. While the rollout is `opt_in` an author can still keep a workflow out by
  not following a checked template and not opting in: under `opt_in` the frame protects the
  checked templates and their followers, and `all` is what protects viewers from any author.
- **Next: `all`**, after a labs deploy, a live check of the priority templates there, and a
  scan of live copies with `workflow_sandbox_scan` (below). Not done here: it needs the
  deploy.

**The scan of live copies: `workflow_sandbox_scan`** (`workflow/management/commands/`,
read-only). It resolves every definition's EFFECTIVE render code (stored copy, followed
template, or a template workflow's published version) and checks it for what the frame
does not give it -- navigations, `top`/`parent`, `confirm`/`prompt`, XHR, `EventSource`,
`import()`, real form submits, jQuery, and every `fetch` to a labs path the route table
refuses (checked against `workflow/sandbox_routes.py`, a Python mirror of `routes.ts` that a
test pins to the TypeScript, so the two cannot drift), plus what only changes (`alert`,
storage, `document.cookie`). Each finding is `breaks`, `changes` or `info`; a fallback a few
lines after its guard (`actions.navigate ? ... : window.location.href = url`) is `info`. Per
definition it says whether it is sandboxed now and would be under `all`. Output is ids,
names, scopes, pattern ids and line numbers: never code. Run it on prod with
`gh workflow run run-labs-command.yml --repo dimagi-internal/connect-labs --ref main --field
command="workflow_sandbox_scan --all-users"` (output in the run's log; `--json`,
`--fix-suggestions`, `--min-severity info` also take). **Coverage, stated plainly:** with no
flags it covers every labs-local (synthetic) definition of every owner and every deployed
seed template. Real-opportunity definitions live on production Connect, readable only per
scope by a member, with no cross-owner listing; `--as-user USERNAME` / `--all-users` read
them with stored Connect tokens across each user's org tree, and anything no scanned user
belongs to is still not covered (the report counts scopes read, refused and failed). On the
seed templates (2026-10-13) the only `breaks` is the deprecated `mbw_monitoring` (its own
`/custom_analysis/` endpoints, `EventSource` with no fallback, `location.reload`); every
checked template is clean of `breaks`.

### Review fixes (phase 2, before merge)

An independent adversarial review of the diff found these; each is fixed with a test that
fails without it (`components/workflow/sandbox/workflow_sandbox.test.js`,
`connect_labs/workflow/tests/test_workflow_sandbox.py`):

1. **A body check could be passed with bytes** (or a byte-order mark, which JavaScript
   refuses to parse and Python accepts). A route that inspects its body now takes only a JSON
   object, as text, parsed by the host; the host sends `JSON.stringify` of what it checked,
   never the frame's bytes.
2. **Scope in the query was unbound on reads**, and the context middleware both answers in
   that scope and saves it to the viewer's session. `opportunity_id`, `program_id` and
   `organization_id` are now bound on every request; a write that names none gets this
   workflow's own scope appended, so it never lands in whatever the session last pointed at.
3. **`/audit/api/audit/preview/` (and audit creation) named opportunities in the body
   unchecked**: now bound.
4. **One yes covered a kind of write against any record.** An effect on one existing record
   (delete an audit, update a task, message the worker on a task, complete or delete the run,
   cancel an audit job) is asked every time and names the record; a yes to a creating kind
   still holds 2 minutes, now for at most 200 writes.
5. **Job cancel was unbound**: it now takes only a job this run is running or this host
   started, and only for this run.
6. **The question could be double-click-jacked**: labs' own question is drawn unlike the
   page's (amber, a shield), focuses Cancel, and Allow works only 700 ms after it appears and
   for a press that began on it.
7. **A sign-in start could carry a second hop** (`?next=` on to an OAuth consent page): a link
   may not carry `next`, `redirect*` or `return*`; CommCare logout is no longer a link target.
8. **A second `runAction` could swap the open dialog**: refused while one is open; the dialog
   is keyed per request.
9. **`setLocation` could rewrite the scope and run** that host code reads off the address:
   `opportunity_id`, `program_id`, `organization_id`, `owning_program_id`, `run_id`, `edit`,
   `sandbox`, `present`, `template_draft` may be kept but not changed.
10. **A "compute-only" job's scope came from the frame**: `job_config` scope ids are bound.
    What remains -- a rollup reading the watched sources named in this run's state -- is a
    cross-workflow read, in the residual below.
11. **`audit-batch-config` could name any opportunity**: its `per_opp` keys are bound.

### Residual risk, phase 2

- **Exfiltration of what a page or workflow shows its viewer** (WebRTC), as above: equal to
  today for a workflow, and now the same for a page (the ruling above).
- **Cross-workflow reads.** The run, history, snapshot-preview and indicator-definition reads
  (and a rollup job reading the watched sources this run's state names)
  are not bound to this workflow, so a workflow's author can read, as a viewer, any run the
  viewer can see through those few routes (then exfiltrate it as above). Today the same code
  can read everything. Follow-up: bind them to the definitions a workflow declares
  (`workflow_sources`, companion links).
- **Reads with ids the host cannot check** (an audit session's bulk data, a task's detail):
  the server checks the viewer's access as always.
- **Clickjacking-style prompts.** An author can time an effect request to a click. The
  question names the kind of write in labs' words; it does not name the rows.

### Verified (locally, 2026-10-12/13), and the rollout state

Against a local server (labs-only opportunity, a fixture of plausible pipeline rows and four
workers per workflow, so both renders get identical data):

- **Every deployed template** (the 41 non-deprecated, non-page ones) drawn plain and with
  `?sandbox=1`: identical visible text for all 41 and no sandbox-only console error, after the
  review fixes. Pages `page_blank` and `supply_program_home` drawn in the unified frame.
- **Clicks**, plain vs sandboxed, identical after every click, on the priority set:
  `supply_stock_review`, `supply_stock_forecast`, `kmc_programme_metrics` (29 clicks: tabs,
  sorts, column-definition dialog, copy, the full statement), `kmc_opp_report`,
  `kmc_flw_review`, the indicator trio, `mbw_auditing_v5`, `audit_with_ai_review`,
  `program_admin_report`, `performance_review`. `performance_review` by hand: a status change
  saved to the run; "Mark Run Complete" made labs ask (the page's text quoted), completed the
  run and re-drew it from its snapshot.
- **Probes on a workflow route** in Chromium, Firefox and WebKit (Playwright), all blocked,
  and no request reached the server: `POST /labs/mcp/tokens/create/` (and a pristine `fetch`
  from an `about:blank` child), the token page, a render-code write, a declared action's run
  endpoint, another run's state, XHR to labs, fetch / sendBeacon / img to another host,
  `document.cookie`, `parent.document`, `top.location`, labs' `localStorage`, `window.open`,
  `location = <other host>` (the frame-src refuses it; Chromium and WebKit then stop the
  frame), `top.location = ...`, an undeclared `runAction`; a click on "create task" made labs
  ask and a no stopped it. `RTCPeerConnection` is there (the residual).

**Marked as checked** (`SANDBOX_CHECKED`, workflow/templates/__init__.py): 40 templates, so
their followers and every new instance run in the frame once this is deployed. Left out:
`kmc_image_audit`, whose render reads audits for the opportunities in its own
`config.opp_meta` rather than its `opportunity_ids` (the frame binds reads to the latter, and
an author-written list cannot widen that); it needs the two reconciled.

**`kmc_image_audit` reconciled (2026-10-13), and checked: all 41.** Ruling: a workflow's
opportunities are its `opportunity_ids`; `config.opp_meta` only *describes* them (LLO,
country, scale hardware) and cannot add one. The render offers and reads only opportunities
in both, drops a saved selection naming any other, and lists the ones its config describes
but it does not cover ("Not offered: ... Add it to the workflow's opportunities"). Why: the
alternative -- letting config declare opportunities -- is exactly the author-written widening
the frame refuses. Cost: a live instance whose `opportunity_ids` are narrower than its
`opp_meta` offers fewer opportunities until `workflow_update_opportunity_ids` adds them (the
note names them). Its server-side schedule (`run_default`, `schedule_defaults.opportunity_ids`)
is unchanged: it runs as the person who saved it, with their access checked by the API, so it
widens nothing; a schedule can still audit an opportunity the dashboard does not show, which
the note makes visible. Checked in Chromium against a local server: drawn sandboxed, selecting
the opportunity and asking which photo types it has made only reads of that opportunity, and
no request was refused. Test: `templates/__tests__/kmc_image_audit_opps.test.js` runs the real
render and records its requests.

**Still to do before `all`:** a labs deploy and a live check there (Jonathan's session, the
priority templates with real data); a scan of live COPIES (own render code) with
`workflow_sandbox_scan --all-users` (Rollout, above) -- its `breaks` rows are what stays out
until fixed or opted in, and its coverage line says what it could not read; then
`LABS_WORKFLOW_SANDBOX=all`. Follow-ups: bind cross-workflow reads to the
definitions a workflow declares; declared actions on pages (a scope instead of a run).
