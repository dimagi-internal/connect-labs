# Supply Chain RFP/EOI System — Operation End Starvation

**Status:** approved design — pre-implementation (2026-07-25)
**Pattern:** second "satellite site" exemplar, following the campaign utility tool playbook

## 1. Context & Goals

Labs has one app — `campaign/` — that treats labs as a **host** rather than a platform: own
auth, own data layer, standalone frontend, self-contained tests, three production imports
from labs total. That pattern is generically useful for standing up independent websites
that could later be split into their own deployments.

This project builds the second app on that pattern: a **supply chain RFP/EOI and
post-award monitoring system** for a demo initiative called **Operation End Starvation
(OES)**. Deliberate decisions inherited from the framing conversation:

- **No abstraction layer, no extraction.** We duplicate/fork whatever campaign-style
  machinery we need. The "framework" for now is the *pattern*, not shared code. A future
  cleanup may split satellite-worthy Django apps out of labs; this app should make that
  easy for itself but must not build the framework speculatively.
- **Auth done properly, data all demo.** Real login/registration screens against the
  shared Django `users.User` table with normal email/username semantics — but every
  account is a seeded demo persona and all data is synthetic.
- **Registry model** for qualification (WFP/UNICEF-style supplier roster): passing EOI
  review stamps per-category qualifications onto the *org*, with expiry. RFPs invite from
  the registry.
- **Award is the midpoint, not the end.** Awarded suppliers report actual supply
  movements; the system renders compelling flow visualizations for four audiences
  (supplier, OES, host government, US Government as ultimate payer).

## 2. Premise & Demo Arc

**Operation End Starvation** — a multi-country famine-response initiative across
**Nigeria, Sudan, Ethiopia, and Burkina Faso**, standing up a supply base in four
categories: **RUTF**, **therapeutic milk (F-75/F-100)**, **road transport**, and
**warehousing**.

Demo arc: procurement lead opens an EOI round → suppliers register, build org profiles,
apply → reviewers qualify them into the supplier registry → an RFP with concrete lots
("60,000 cartons RUTF → Maiduguri by 15 Sept") goes to qualified suppliers → per-lot bids
→ scoring → per-lot awards → contracts → shipments flow factory → port → warehouse →
distribution hub → every stakeholder sees their slice, up to the US Government asking
"what did our money buy?"

## 3. App Boundary & Wiring

New Django app `connect_labs/supply/`, mounted at `/supply/`, URL namespace `supply`.

| Touchpoint | Decision |
| --- | --- |
| `INSTALLED_APPS` | Registered in `local.py`, `labs_aws.py`, `test.py` (mirror campaign; not `base.py`) |
| Auth | Standard Django sessions against shared `users.User`. Server-rendered signup/login/logout pages under `/supply/`. Password-based. Open registration for suppliers; staff accounts seeded only. No OAuth. |
| Labs host contract | Add `/supply/` to `_SKIP_PATH_PREFIXES` in `connect_labs/labs/oauth_session.py` so labs' OAuth middleware leaves supply sessions alone. Matching host-integration test in the supply suite (campaign's `test_host_integration.py` pattern). |
| Static / templates | `connect_labs/static/supply/`, `connect_labs/templates/supply/`. Templates are standalone `<!doctype html>` documents — no labs base template, no labs context processors relied upon. |
| Frontend build | `webpack/build-supply.js` Babel-concat (campaign's `build-campaign.js` pattern): ordered non-module JSX files transpiled with classic runtime, concatenated to `static/bundles/js/supply-bundle.js`. `npm run build:supply` / `watch:supply`, chained into `build` / `dev`. Registered in `.github/workflows/build-node.yml` paths **and** the `fe-hash` find list (campaign missed the latter — known gap #9 from the review). |
| Tables | All models use explicit `db_table = "supply_*"`. Own migrations only. |
| Labs imports | `users.User` only (plus Django/GeoDjango framework). Explicitly **not** used: `AdminBoundary`, `LabsRecordAPIClient`, `LocalLabsRecord`, workflow engine, labs context middleware, Connect OAuth. |

## 4. Domain Model

All tables `supply_*`. Category is a fixed choices enum: `rutf`,
`therapeutic_milk`, `transport`, `warehousing`.

### Procurement side

- **SupplierOrg** — legal name, registration number, country, HQ city, description,
  contact fields. **SupplierMember** links user → org (one org per user in v1).
- **Certification** — org FK, type (ISO 22000, GMP, UNICEF RUTF approval, …), issuer,
  expiry date, document name (demo stub; no real file uploads).
- **EOIRound** — title, brief, open categories, open/close dates, status
  `draft → open → closed`.
- **EOISubmission** — org × round, categories applied for, per-category commitments
  (capacity, regions served, lead time), **`profile_snapshot` JSON frozen at submit**
  (reviewers evaluate the snapshot, never the live profile), status
  `draft → submitted → qualified / rejected`.
- **EOIReview** — submission FK, reviewer, per-category decision + notes.
- **Qualification** — org × category, source submission, granted/expires dates, status.
  The set of live qualifications **is** the registry.
- **RFP** — title, brief, target categories + countries, bid deadline, status
  `draft → published → closed → awarded`.
- **Lot** — RFP FK, category, description, quantity + unit, delivery location
  (country + place name + optional node FK), delivery deadline.
- **Bid** — org × RFP, status `draft → submitted`; **LotBid** children — lot, unit price,
  currency, lead-time days, notes. **BidScore** — lot-bid FK, reviewer, technical score +
  notes; financial score derives from price rank.
- **Award** — lot → winning LotBid, awarded by/at. The decision record.

### Execution side

- **SupplyNode** — physical facility with PostGIS point: kind
  `factory / port / warehouse / distribution_hub / delivery_point`, name, country
  (ISO code; transit nodes may sit in non-member countries, e.g. Djibouti, Togo),
  owner (supplier org FK nullable — null means OES-network facility).
- **Contract** — created from an Award; total quantity, value, currency, delivery
  schedule, status. The execution container (Award stays immutable as the decision).
- **Shipment** — contract FK, origin node → destination node, ordered waypoint node list
  (JSON of node IDs), quantity, value, status
  `planned → in_transit → delivered → confirmed`, departed / ETA / delivered dates.
  The atom the flow map renders.
- **DeliveryReport** — shipment FK, submitting user, quantity delivered, delivery date,
  notes, discrepancy flag. Supplier-submitted; submitting one advances the shipment
  lifecycle. This is the "supply data from awarded suppliers."
- **AuditLog** — campaign-style append-only log on every privileged write.

### Access control

- **StaffRole** — user → `procurement_admin` | `reviewer` | `gov_observer` (with country
  field) | `funder`. Seeded only, never self-registered.
- Supplier role is implied by SupplierMember.

## 5. Roles & Permissions

| Role | Can |
| --- | --- |
| supplier | Manage own org profile/certs; submit EOIs; view own submissions/qualifications; bid on published RFPs where org holds a live matching-category qualification; view own contracts/shipments; submit delivery reports; supplier ops map view |
| reviewer | EOI review queue + per-category decisions; bid technical scoring |
| procurement_admin | Superset of reviewer + create/publish/close rounds and RFPs; registry browser; award lots; create contracts/nodes; full OES command view; audit log |
| gov_observer | Read-only, country-scoped: flows touching their country, in-country dashboards |
| funder | Read-only, global: money-denominated views |

Enforced by a server-side `rbac.py` role × module × verb matrix; mirrored in a client
`perms.js` for show/hide only; the two locked together by a contract test that parses
`perms.js` (campaign's `test_rbac_contract.py` pattern). Cross-org isolation (suppliers
see only their own org) and country scoping (gov observers) enforced in every queryset
and covered by explicit tests.

## 6. Screens (one SPA, role-gated)

**Supplier portal:** dashboard (qualifications, open rounds, active RFPs, deadlines) ·
org profile + certifications editor · EOI wizard (categories → per-category commitments →
review + submit with snapshot) · my submissions · RFP list (qualified only) · bid
workspace (per-lot pricing table) · my bids & awards · **supplier ops view**: my
contracts, shipment pipeline, map of my routes with statuses, delivery-report form,
on-time/fulfillment gauges.

**Procurement console:** dashboard KPIs (registry size by category, rounds in flight,
bids pending, shipments in transit) · EOI round management · review queue → submission
detail (frozen snapshot beside per-category decision form) · **registry browser** (filter
by category/country/expiry) · RFP builder with lots · **bid comparison table per lot**
(price-ranked, technical scores inline) · award confirmation · contract + node management
· audit log.

**The four visualization surfaces** (the differentiating demo content):

1. **Supplier ops view** — my routes and shipment statuses on the map (part of the
   supplier portal above).
2. **OES command view** — hero screen: full network flow map with animated arcs
   (factory → port → warehouse → distribution hub; arc width = volume, color =
   category/status), pipeline vs. need by country, registry health, contract burn-down,
   exception feed (late shipments, discrepancy flags).
3. **Host-government view** — country-scoped: inbound ports, in-country warehouse levels,
   distribution reach by admin-1 choropleth, delivered tonnage over time.
4. **US Government / ultimate-payer view** — money-first: appropriated → contracted →
   shipped → delivered waterfall, cost per delivered carton by category and country,
   famine-zone population reached, global arc map framed as "where US dollars became
   food." Aesthetic bar: screenshot-able for a congressional briefing.

## 7. Geo & Visualization Stack

- **Nodes** are lat/lng PostGIS points; **flows** are arcs between nodes (with waypoint
  chains for multi-leg routes).
- Country and admin-1 outlines: **vendored public GeoJSON shipped as static files** —
  deliberately *not* labs' `AdminBoundary` table (campaign's #1 split blocker; and Sudan/
  Ethiopia/Burkina Faso aren't loaded there anyway).
- **Mapbox GL** (existing `MAPBOX_TOKEN` setting) + **deck.gl** via CDN UMD scripts
  (consistent with campaign's React-from-CDN approach): `ArcLayer` / `TripsLayer` for
  animated flows, `GeoJsonLayer` for choropleths.
- Landlocked realism: Ethiopia's corridor runs through Djibouti; Burkina Faso's through
  Lomé/Tema. Transit nodes in non-member countries are first-class and make the map
  honest.

## 8. Seed — `seed_supply_demo`

Idempotent management command, fixed PRNG, deterministic output (campaign's seed
pattern + determinism test). Builds the entire demo world:

- ~16 supplier orgs across the four countries + a few international (mixed quality:
  strong certs, expiring certs, thin profiles).
- Staff personas: OES procurement lead, reviewer, Nigeria gov observer, US funder —
  plus one supplier login. All with known demo passwords for live driving.
- One **closed** EOI round that populated the registry; one **open** round with
  submissions in every status (review-queue demo).
- One **published** RFP (3 lots, 4–6 bids each, partially scored — the comparison table
  lands mid-story); one **awarded** RFP as history.
- ~30 supply nodes: RUTF factories (Kano, Addis Ababa, Lagos), ports (Lagos, Port Sudan,
  Djibouti, Lomé), regional warehouses, distribution hubs in famine zones (Maiduguri,
  El Fasher, Gode, Djibo).
- 3 contracts in execution, ~40 shipments across all statuses with realistic waypoint
  routes and several weeks of delivery-report history — every viz surface lands populated
  and the animated flow map has motion.

## 9. Testing Strategy

Campaign's `TESTING_STRATEGY.md` approach, ported:

- Self-contained `tests/` with factories; zero labs fixtures.
- RBAC contract test (`perms.js` ↔ `rbac.py` equality).
- Cross-org isolation and gov-country scoping tests.
- Lifecycle tests: snapshot immutability after submit; can't bid without live matching
  qualification; can't award an unsubmitted bid; delivery report advances shipment;
  award → contract linkage.
- Seed determinism + migration lockstep tests.
- Host-integration test: labs OAuth middleware must skip `/supply/`.
- Run against real Postgres/PostGIS locally (standard labs local test setup).

## 10. Satellite-Site Pattern (conventions this app follows)

Recorded here as the seed of a future formalization — not built as shared code now:

1. Own auth, own session semantics, path-scoped; host middleware must skip the app's
   prefix (host contract + test).
2. Own models with app-prefixed `db_table`; own migrations; no labs data-layer imports.
3. Standalone HTML shells; no labs base templates or context-processor reliance.
4. Own Babel-concat frontend build producing one bundle; registered in node build CI
   (paths **and** content-hash list).
5. Server-side RBAC matrix + client mirror + contract test.
6. Idempotent deterministic seed command that builds the full demo world.
7. Self-contained test suite designed to travel with the app.
8. Minimal, enumerated labs imports (target: `users.User` only).

## 11. Out of Scope (YAGNI)

- Real file uploads (certification documents are name stubs).
- Payments/invoicing, contract amendments, multi-currency conversion.
- Email/notifications.
- Multi-org membership per user; org user management UI.
- Login-approval workflow (supplier registration is open; staff are seeded).
- Any shared "satellite framework" code, scaffolding, or extraction from labs.
- MCP tools for this app (can come later if authoring pain appears).

## 12. Implementation Phasing (for the plan)

1. **Procurement core** — app scaffold, auth pages, models/migrations, RBAC, org
   profiles, EOI rounds/submissions/review, registry, RFP/lots/bids/scoring/award, seed
   v1, tests.
2. **Supply execution** — nodes, contracts, shipments, delivery reports, supplier ops
   view, seed v2.
3. **Visualization surfaces** — OES command view, gov view, funder view; deck.gl flow
   map; polish pass to the "congressional briefing" bar.
