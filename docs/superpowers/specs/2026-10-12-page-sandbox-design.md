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
readable by the page's author. Phase 1 therefore gives pages no visit-level pipelines and no
`queryPipelineRows`: a page draws scope, Settings, supply figures (aggregates, no personal
data) and other workflows' run summaries. Phase 2 has to decide what workflows may read with
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
  also on `labs:workflow:run` and `supply_chain:workflow_view`, where a page is drawn too;
  it reaches `page_context` only, which refuses anything that is not a sharing page.
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

Verified in Chromium (gstack browse) against a local server: every attempt on the live
test list failed (fetch to labs and to another host, `document.cookie`, `parent.document`,
labs' `localStorage`, an off-site `<img>`, `window.open`, `alert`, `location = ...`, which
the host caught as "this page tried to leave its frame"), and `page_blank` drew, its links
navigated on a click, the frame resized, `view.confirm` drew the host dialog, and
`shareSelection` reached `canopyHost` with the injected `resource` dropped. Firefox and
Safari by hand, and the same on labs after a deploy, remain to do before a real
organisation gets a landing page.

## Phase 2: workflows (not built here)

The same frame and bridge, plus: the phase-2 methods; images that need the session
(visit photos) fetched by the host and passed in as `blob:` URLs; job progress over the
bridge; `window.LabsAudit`'s fetch moved behind a method; the 76 direct `fetch(` calls in
21 seed templates moved onto bridge methods. Every write goes through a method the host
maps to a known endpoint, with the host's own preview/confirm where one exists. Also left
from phase 1: `ConnectMap`'s admin boundaries (fetched from labs, so blocked in the frame;
a bridge method or host-fetched GeoJSON), and Leaflet tile servers (not in `img-src`).
