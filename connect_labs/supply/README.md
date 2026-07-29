# OES Supply — the supply-chain satellite site

A standalone website at **`/supply/`** for **Operation End Starvation (OES)**, an
invented multi-country famine-response initiative. It runs two-stage
humanitarian procurement (expression of interest → supplier registry → RFP →
per-lot award), then keeps going: awarded suppliers report what physically
moves, and four stakeholder dashboards render it.

Everything in it is synthetic. There is no real Connect or CommCare data
anywhere in this app.

Live at `https://labs.connect.dimagi.com/supply/`.

---

## Why it is built this way

This is the **second "satellite site"** in labs, after `campaign/`. A satellite
treats labs as a _host_, not a platform: it brings its own auth, data layer,
RBAC, frontend build and tests, so it could be lifted into its own deployment
without unpicking it from labs.

Supply takes that further than campaign did — it has **zero direct cross-app
imports**. It reaches the shared user table through `get_user_model()` and
`settings.AUTH_USER_MODEL` rather than importing `users.User`, where campaign
imports three things. Its only contact with labs is:

1. an `INSTALLED_APPS` entry in `local.py` / `labs_aws.py` / `test.py`
   (deliberately **not** `base.py`, mirroring campaign),
2. one line in `config/urls.py`,
3. `"/supply/"` in the `LABS_SATELLITE_URL_PREFIXES` setting (`config/settings/base.py`),
   which `connect_labs/labs/oauth_session.py` reads. See [docs/multi-site-auth.md](../../docs/multi-site-auth.md).

That third one is a **host contract**, not an implementation detail. Labs' OAuth
middleware logs out any authenticated user whose `session["labs_oauth"]` is
missing — which is every supply user, since supply uses plain Django sessions.
Without the skip, supply users get logged out on every request. Campaign learned
this the hard way (PR #661). `tests/test_host_integration.py` asserts the skip
exists, so a future change to labs cannot silently break this app.

Deliberately **not** used: `AdminBoundary` (campaign's #1 split blocker),
`LabsRecordAPIClient`, `LocalLabsRecord`, the workflow engine, labs context
middleware, Connect OAuth.

Design record: `docs/superpowers/specs/2026-07-25-supply-chain-rfp-eoi-design.md`.

---

## Layout

```
connect_labs/supply/
  models/          procurement.py | execution.py | demand.py   by lifecycle stage
  serializers/     procurement.py | execution.py | demand.py   the SPA's wire contract
  services/        the business rules — API modules stay thin
    ingestion/     _core.py + epcis.py | asn.py | manual.py  (see below)
    cover.py       stock, burn, weeks of cover, children at risk, expiry risk
    exceptions.py  the command-centre queue, ranked in one unit
    coverage.py    delivery against caseload; courses vs recorded recoveries
    actions.py     reallocation, expedite, raising a shortfall
    eoi_actions.py rfp_actions.py org_actions.py tokens.py
  api/             thin JSON views: bootstrap, orgs, eoi, rfp, execution, ingest, demand
  demo/            data.py (the narrative) + organisations | solicitations | execution | demand
  routes.py        digitised road corridors and sea lanes for the flow map
  gs1.py           SSCC / GTIN / GLN check digits, and the carton→MT→children ladder
  rbac.py roles.py decorators.py audit.py
  tests/           self-contained; no labs fixtures

connect_labs/static/supply/     JSX + css/ (split, concatenated by the build)
connect_labs/templates/supply/  standalone <!doctype html>, no labs base template
webpack/build-supply.js         Babel-concat build for both JS and CSS
```

Nothing exceeds ~350 lines. The models, serializers and ingestion packages all
re-export from `__init__`, so `from connect_labs.supply.models import X` works
regardless of which half X lives in.

---

## The domain in one pass

**Procurement.** An `EOIRound` opens; a `SupplierOrg` submits an
`EOISubmission` naming the categories it wants and what it commits to. On
submit, a **snapshot of the org profile is frozen onto the submission** —
reviewers assess that snapshot, never the live profile, so a supplier editing
their profile afterwards cannot change what was judged. A passing `EOIReview`
stamps `Qualification` rows onto the _org_, per category, with an expiry. The
set of live qualifications **is** the supplier registry.

An `RFP` carries `Lot`s. A supplier sees an RFP only while holding a live
qualification in one of its categories — `rfp_actions.org_can_bid` is the single
source of truth for that. Bids are per-lot (`LotBid`), scored by reviewers
(`BidScore`), and awarded lot by lot (`Award`).

**Execution.** An `Award` is the immutable decision; a `Contract` is the
container that carries it out, drawing against an `Appropriation`. `Shipment`s
move between `SupplyNode`s along a stored route, carrying `ShipmentLine`s with
GTIN, batch/lot and expiry.

**Shipment status and milestone actuals are DERIVED from the append-only
`SupplyEvent` log and never set by hand.** That is the load-bearing invariant of
this app. `Milestone` keeps planned / estimated / actual as three separate
timestamps (DCSA's PLN/EST/ACT), so "late" is a real measurement rather than a
vibe. A receipt that does not reconcile raises a `Discrepancy`.

**Demand.** The third stage, and the denominator the other two lack. A
`CaseloadEstimate` says how many severely malnourished children a district is
expected to have this month, joined to the IPC choropleth by `adm1_code` and
carrying its own derivation in `source_note`. A `SupplyNode` that carries an
`adm1_code` is answerable for children; one that does not (a port, a national
warehouse) sits on the route without a denominator, and reporting cover there
would be meaningless rather than zero.

Everything downstream is derived from those two facts in **one place**,
`services/cover.py`: stock on hand from the event log, weekly burn from the
admission rate, weeks of cover, the projected stockout date, children at risk
from a delay, and cartons that will expire before the caseload can consume
them. The command centre and the implementing-partner surface both render what
it returns, because the two must agree about the same node and two
implementations would drift on first touch. It is also why exception severity
is no longer computed in `tab_command.jsx` — a ranking that decides which
children go without belongs somewhere pytest can reach.

`services/exceptions.py` builds the command-centre queue from all of that.
**Every row carries the same unit: children who miss a full course.** Four
different things can be wrong — a late consignment, a short receipt, an
expiring batch, a partner's shortfall — and that is the only quantity they
share, so it is the only one that makes ranking them against each other an
ordering rather than an accident. Each row also carries the derivation that
produced its rank.

A `SupplyAction` is append-only (enforced in `save`/`delete`) and requires a
rationale. A reallocation is not a status change: it creates a real `Shipment`
from the surplus node with planned milestones and stored geometry, so the
downstream figures recompute _from_ it. It refuses to overdraw the source,
because a paper transfer leaves the receiving site planning against cartons
that never arrive.

The last mile — `DistributionRecord` and `ChildOutcome` — is **synthesised
inside supply**, deliberately. Importing Connect service-delivery data would
end the zero-cross-app-import property this app is built on. Outcomes are
labelled synthetic in the payload itself rather than only in a template, and
discharges are seeded to the Sphere performance band for SAM treatment
(recovery above 75%, defaulting below 15%) so the gap between courses delivered
and recoveries recorded has a size somebody can defend.

---

## Ingestion: three tiers, and why

Awarded suppliers report movements through `/supply/api/v1/`, authenticated by
an org-scoped bearer token (hashed at rest, shown once).

| Tier      | Endpoint               | What it is                                                                                          |
| --------- | ---------------------- | --------------------------------------------------------------------------------------------------- |
| `epcis`   | `POST /epcis/capture/` | GS1 **EPCIS 2.0** (ISO/IEC 19987) JSON-LD — the standard visibility-event format                    |
| `asn`     | `POST /shipments/`     | A **despatch advice**: the shipment → packages → items tree of an X12 856 / EDIFACT DESADV, as JSON |
| `checkin` | `POST /checkins/`      | A consignment reference, a place, what happened. A driver's phone is enough                         |

Plus `GET /shipments/<id>/events/` for push/pull parity.

The three tiers exist because that is the **real capability gradient** of a
humanitarian supply chain, established by a research pass over GS1, EDI and
health-supply-chain practice. UNICEF's 2024 packing specs already require GS1
logistics labels on RUTF shipments, so tiers 1–2 are current practice, not
aspiration — but Sudan has no domestic producer and its corridor runs on paper
waybills, so tier 3 is not a fallback, it is the honest case.

**Every tier has a webform equivalent** in the supplier portal, posting the same
payload to the same services. Only `source_tier` differs, and hand-keyed data is
labelled "Entered by hand" wherever it appears. `test_execution.py` asserts both
doors produce field-for-field identical records.

Capture is **idempotent on `external_id`** because delivery is at-least-once.

---

## Roles

| Role              | Surface                                                                     |
| ----------------- | --------------------------------------------------------------------------- |
| supplier          | own org, EOI, bidding, operations, integration tokens                       |
| partner           | own sites, distribution calendar, cover, raising shortfalls, outcomes       |
| reviewer          | review queue, bid scoring, registry, command centre                         |
| procurement_admin | the above plus rounds, RFPs, awards, discrepancy resolution, supply actions |
| gov_observer      | one country's view, read-only, **scoped server-side**                       |
| funder            | money view, read-only                                                       |

The **partner** is an implementing organisation — it runs the feeding sites. It
is neither a supplier (it never bids, so no `eoi` or `bids`) nor an observer
(it acts: it receives stock, raises shortfalls, records what it distributed).
It is a `kind` discriminator on `SupplierOrg` rather than a parallel model, so
membership, authentication and API tokens stay single-path. A partner's sites
are scoped in the bootstrap payload the same way the government observer's
country is: another partner's sites never reach the page.

`rbac.py` is the real gate; `static/supply/perms.js` mirrors it for show/hide
only, and `test_rbac_contract.py` parses the JS literal and asserts equality —
so they cannot drift. That test is itself guarded by one that perturbs the
literal and checks the parser notices.

The government observer's country filter is applied **in the bootstrap payload**,
not the browser: another country's consignments never reach the page.

---

## The demo world

`python manage.py seed_supply_demo [--reset]`

Idempotent and deterministic (fixed PRNG). `demo/data.py` holds the narrative —
edit that to change the story, not the seeding code.

Geography mirrors reality: RUTF plants in Kano, Lagos, Ouagadougou and Addis
Ababa, **none in Sudan** (supplied through Port Sudan), and corridors through
Djibouti and Lomé because Ethiopia and Burkina Faso are landlocked. Organisation
names are fictional.

Five personas, all sharing one password:

- `oes-lead@oes.example` — procurement admin (everything, incl. command centre)
- `oes-review@oes.example` — reviewer
- `gov-ng@oes.example` — Nigeria observer
- `usg@oes.example` — funder
- `supplier@savanna.example` — Savanna Nutrients (Kano)

**The password in the repo is `oes-demo-2026`, and it is deliberately rejected on
the deployed site.** `/supply/` has open registration on a public host and the
seed creates a procurement admin, so labs reads `SUPPLY_DEMO_PASSWORD` from
Secrets Manager (`labs-jj-supply-demo-password`, referenced from
`deploy/task-definitions/{web,worker}.json`). Reseeding rotates existing users'
passwords, so changing the secret takes effect on the next seed.

---

## The map

Mapbox GL v3 (`dark-v11`) with deck.gl 9.3 via `MapboxOverlay`, both pinned,
loaded from CDN in `templates/supply/app.html`.

Three things here were learned painfully and should not be undone casually:

- **Mapbox's stylesheet must load before ours.** Its `.mapboxgl-map { position:
relative }` overrides `.flowmap { position: absolute }` and collapses the map
  container to zero height — leaving a canvas in the DOM and a blank rectangle
  on screen. The CSS also uses `.flowmap-wrap > .flowmap` to win on specificity
  regardless of load order.
- **Overlay mode, not interleaved.** Interleaved would let Mapbox labels sit
  above the flows, but it renders nothing under software WebGL, which is what
  headless verification runs on. A prettier map that cannot be checked is the
  worse trade.
- **A persistent faint `PathLayer` under the animated `TripsLayer`.** Without
  it the network only exists wherever a comet happens to be, and the map reads
  as scattered dots rather than a supply chain.

Animation advances `currentTime` only — `data` and accessors stay stable between
frames. Routes come from `routes.py` (hand-digitised corridors; the Lagos–Port
Sudan lane rounds the Cape and transits Bab-el-Mandeb) and are baked onto
shipments at seed time, never computed live.

The IPC famine-phase choropleth uses the **official FEWS NET palette**, and
those colours are reserved for IPC and nothing else. Boundaries are vendored
Natural Earth admin-1 in `static/supply/geo/` — deliberately not labs'
`AdminBoundary`, which is campaign's biggest split blocker and lacks three of
our four countries.

Without a Mapbox token the map renders an explanation rather than taking the
page down.

---

## Testing

124 tests, self-contained: own factories and `conftest.py`, **zero labs
fixtures**, so the suite travels with the app if it is ever split out.

```bash
DATABASE_URL=postgis://postgres:postgres@localhost:5432/connect_labs \
  GDAL_LIBRARY_PATH=/opt/homebrew/lib/libgdal.dylib \
  GEOS_LIBRARY_PATH=/opt/homebrew/lib/libgeos_c.dylib \
  pytest connect_labs/supply/
```

What each file is defending:

| File                          | Guards                                                                                                                 |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `test_host_integration.py`    | labs' OAuth middleware skips `/supply/`                                                                                |
| `test_rbac_contract.py`       | `perms.js` ↔ `rbac.py` equality, plus a guard proving the parser detects drift                                         |
| `test_eoi.py` / `test_rfp.py` | snapshot immutability; no bidding without a _live_ qualification; no awarding an unsubmitted bid or a lot twice        |
| `test_ingestion.py`           | EPCIS/ASN/check-in capture, idempotency, monotonic status, discrepancy on short receipt, pull parity, GS1 check digits |
| `test_execution.py`           | **webform ↔ API equivalence**, cross-org isolation, org-scoped tokens                                                  |
| `test_dashboards.py`          | gov country scoping, funder money stages, route geometry on the wire, and that disbursement never exceeds obligation   |
| `test_seed.py`                | determinism, execution-state preservation on reseed, corridor geometry, password rotation                              |

**Two lessons worth keeping.** Several real bugs in this app were invisible to
unit tests and only showed up by driving a browser — the map rendering nothing,
the funder view reporting $583M disbursed against $4.9M obligated (a
truck-month price multiplied by a carton count), status chips showing raw enum
values. Drive the thing before believing it works.

And: a blank-GLN guard was once "fixed" by a patch that silently no-oped against
reformatted source, and shipped as done because **it had no test**. If a fix
matters enough to claim, it matters enough to assert.

---

## Frontend build

```bash
npm run build:supply     # or watch:supply; chained into build / dev
```

Non-module scripts sharing one global scope in a fixed load order, Babel-
transpiled with the _classic_ JSX runtime and concatenated — the same shape as
campaign, and deliberately not webpack, whose per-module scope would break the
global-sharing contract. The CSS is split by concern and concatenated the same
way; the numeric filename prefixes **are** the cascade order.

Both bundles are gitignored and built into the node image, so **a JSX or CSS
change needs the node image rebuilt before the deploy** — `build-node.yml`
triggers on `static/supply/**` and includes it in the content hash.

---

## Deploying

Deploy only from `main`:

```bash
gh workflow run deploy-labs.yml --repo dimagi-internal/connect-labs \
  --ref main --field run_migrations=<true if migrations>
```

Then seed via ECS exec (container is `web`, not `connect-labs`):

```bash
aws --profile labs --region us-east-1 ecs execute-command \
  --cluster labs-jj-cluster --task <arn> --container web \
  --interactive --command "python manage.py seed_supply_demo --reset"
```

Task definitions are version-controlled in `deploy/task-definitions/` and
registered from the repo on every deploy — editing them in the AWS console has
no lasting effect.

---

## Not built, on purpose

Real file uploads; payments/invoicing; email; **outbound** webhook delivery
(inbound and pull parity are built, push is simulated); raw X12/EDIFACT parsing;
AS2; GDSN; sachet-level serialisation; live route computation; multi-org
membership; MCP tools. The only unbuilt thing that was ever in scope is a
narrated guided tour of the flow map, always marked stretch.
