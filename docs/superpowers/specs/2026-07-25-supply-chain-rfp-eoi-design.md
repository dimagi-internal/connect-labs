# Supply Chain RFP/EOI System — Operation End Starvation

**Status:** approved design — pre-implementation (2026-07-25, rev 2 after standards + viz research)
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
- **Award is the midpoint, not the end.** Awarded suppliers push shipment data through a
  standards-grounded ingestion API; the system renders compelling flow visualizations for
  four audiences (supplier, OES command, host government, US Government as ultimate payer).
- **Standards-grounded, capability-gradient realism.** The data-exchange layer follows
  real supply chain standards (GS1 EPCIS 2.0, SSCC/GTIN/GLN, ASN shape, IATI), and the
  demo deliberately shows the *actual* capability gradient of RUTF supply chains — clean
  event feeds from the most digital suppliers, sparse phone check-ins on the hardest
  corridors — because that reads as researched rather than naive.

## 2. Premise & Demo Arc

**Operation End Starvation** — a multi-country famine-response initiative across
**Nigeria, Sudan, Ethiopia, and Burkina Faso**, standing up a supply base in four
categories: **RUTF**, **therapeutic milk (F-75/F-100)**, **road transport**, and
**warehousing**.

Real-world grounding (from research; the demo mirrors this shape with fictional org
names): UNICEF procures ~80% of global RUTF under long-term arrangements with ~23
approved manufacturers, most in countries of need — Nigeria has RUTF plants in Kano and
Lagos, Burkina Faso in Ouagadougou, Ethiopia in Addis Ababa; **Sudan has no domestic
producer** and is supplied through Port Sudan. Ethiopia's import corridor runs through
Djibouti; Burkina Faso's through Lomé/Tema. The **unit ladder** is the universal
currency and appears on every screen: *sachets → cartons (150 × 92 g sachets/carton) →
metric tonnes → children treated (~1 carton ≈ one child's full treatment)*.

Demo arc: procurement lead opens an EOI round → suppliers register, build org profiles,
apply → reviewers qualify them into the supplier registry → an RFP with concrete lots
("60,000 cartons RUTF → Maiduguri by 15 Sept") goes to qualified suppliers → per-lot bids
→ scoring → per-lot awards → contracts → shipment events stream in (EPCIS from the Kano
factory, an ASN at despatch, sparse check-ins on the Port Sudan → El Fasher leg) → four
stakeholder views light up, ending with the US Government's "what did our money buy?"

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

## 4. Standards Grounding (data-exchange design)

From the standards research, five standards shape the design; everything else is
deliberately skipped (raw X12/EDIFACT parsing, GDSN, sachet serialization, AS2, FHIR).

1. **GS1 EPCIS 2.0** (ISO/IEC 19987:2024, JSON-LD) — the event envelope for "what/when/
   where/why" supply chain events, with a standardized capture endpoint and
   webhook-subscription semantics. Our ingestion endpoint accepts EPCIS-shaped documents.
2. **GS1 identifiers** — **SSCC** (pallet "license plate"), **GTIN** + batch/lot + expiry
   (product at carton level), **GLN** (parties and locations). UNICEF's 2024 packing
   specs already require GS1 logistics labels on RUTF shipments, so this is current
   practice, not aspiration.
3. **The ASN / despatch-advice shape** (X12 856 / EDIFACT DESADV): the invariant
   `shipment → orders → packages → items` tree sent at despatch time. We accept it as
   JSON (what any commodity EDI-translation layer emits), keyed by SSCC/GTIN/GLN.
4. **Modern webhook delivery contract** — topic-named events, HMAC signatures,
   at-least-once + idempotency on event ID, pull-API parity. Plus DCSA's
   **PLN/EST/ACT** (planned/estimated/actual) classifier on every milestone — this
   powers ETA-vs-plan rendering.
5. **IATI 2.03** for funder traceability: awards/contracts carry an IATI activity ID and
   the funder view renders the disbursement → incoming-funds → expenditure chain —
   explicitly framed as "the linkage IATI specifies but the sector rarely achieves."

## 5. Domain Model

All tables `supply_*`. Category is a fixed choices enum: `rutf`, `therapeutic_milk`,
`transport`, `warehousing`.

### Procurement side

- **SupplierOrg** — legal name, registration number, country, HQ city, description,
  contact fields, **GLN, GS1 company prefix** (demo-valid formats). **SupplierMember**
  links user → org (one org per user in v1).
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
- **Lot** — RFP FK, category, description, quantity + unit (cartons/MT), delivery
  location (country + place name + optional node FK), delivery deadline.
- **Bid** — org × RFP, status `draft → submitted`; **LotBid** children — lot, unit price,
  currency, lead-time days, notes. **BidScore** — lot-bid FK, reviewer, technical score +
  notes; financial score derives from price rank.
- **Award** — lot → winning LotBid, awarded by/at. The immutable decision record.

### Execution side

- **SupplyNode** — physical facility with PostGIS point: kind
  `factory / port / warehouse / distribution_hub / delivery_point`, name, country
  (ISO code; transit nodes may sit in non-member countries — Djibouti, Togo), **GLN**,
  owner (supplier org FK nullable — null means OES-network facility).
- **Appropriation** — a funder money envelope (funder name, amount, fiscal year,
  IATI activity ID). Roots the funder Sankey; contracts draw against it.
- **Contract** — created from an Award; appropriation FK, total quantity, value,
  currency, delivery schedule, status, **IATI activity ID**. Obligated / disbursed /
  delivered amounts are derived, never collapsed into one number.
- **Shipment** — contract FK, origin node → destination node, ordered waypoint node
  list, **route geometry** (precomputed LineString: sea legs via searoute-generated
  paths, road legs from stored corridor polylines — computed at seed/creation time,
  never live), quantity (cartons + derived MT), value, ASN reference, status
  `planned → in_transit → delivered → confirmed` (derived from events, not hand-set).
- **ShipmentLine** — shipment FK, GTIN, batch/lot code, expiry date, cartons.
- **Milestone** — shipment × node × kind (`depart` / `arrive`): **planned, estimated,
  actual** timestamps (DCSA PLN/EST/ACT). Powers the ETA-delta chips and lateness
  exceptions.
- **SupplyEvent** — append-only, EPCIS-shaped event log and the single source of truth
  for execution state: event type (`object` / `aggregation` / `transformation`),
  bizStep (`commissioning / packing / loading / departing / arriving / receiving /
  inspecting`), disposition, event time, read-point node FK, shipment FK (nullable),
  epc/quantity payload JSON (SSCCs, GTIN + lot + cartons), business-transaction refs
  (PO, ASN), **source tier** (`epcis` / `asn` / `checkin` / `portal`), external event ID
  (idempotency key), raw payload JSON. Shipment status and Milestone actuals are
  advanced by events.
- **Discrepancy** — raised when a receiving event's quantities don't reconcile with the
  ASN/shipment lines (or is flagged manually); feeds the exception queue.
- **AuditLog** — campaign-style append-only log on every privileged write.

The old "DeliveryReport form" survives as a **portal form that emits `portal`-tier
SupplyEvents** — manual entry is just the lowest ingestion tier, not a separate model.

### Access control

- **StaffRole** — user → `procurement_admin` | `reviewer` | `gov_observer` (with country
  field) | `funder`. Seeded only, never self-registered.
- Supplier role is implied by SupplierMember.

## 6. Ingestion API (the "real-time supplier feed")

Design: **EPCIS at the core, ASN at the front door, webhooks as the transport, IATI as
the money spine** — with a deliberate capability gradient.

- **`POST /supply/api/v1/epcis/capture`** — accepts EPCIS 2.0 JSON-LD `EPCISDocument`
  payloads: ObjectEvents (the workhorse), AggregationEvents (cartons → pallet SSCC), and
  TransformationEvents (production inputs → RUTF lot — the factory-provenance demo
  beat). Identifiers as GS1 Digital Link URIs or `urn:epc:` forms; class-level
  `quantityList` (GTIN + lot + cartons) alongside pallet-level SSCCs.
- **`POST /supply/api/v1/shipments`** — a despatch-advice JSON document mirroring the
  ASN tree (`shipment → orders[] → packages[] → items[]`) with qualified identifiers
  (SSCC/GTIN/GLN), batch/expiry, carrier, ETA, PO ref, IATI activity ID. Creates the
  Shipment + ShipmentLines + planned Milestones. This is what any supplier behind a
  commodity EDI-translation layer emits unchanged.
- **`POST /supply/api/v1/checkins`** — the low-tech tier: phone-app geo-pings and manual
  leg confirmations (consignment-number-shaped, RITA-style). Deliberately first-class:
  the Sudan corridor arrives this way in the demo.
- **Webhook contract (both directions, demo-simplified):** topic-named events
  (`shipment.despatched`, `shipment.milestone`, `shipment.eta_changed`,
  `shipment.exception`, `shipment.delivered`), HMAC signature + event-ID headers,
  at-least-once semantics with idempotent capture on external event ID, and pull parity:
  `GET /supply/api/v1/shipments/{id}/events` returns exactly what webhooks would push.
  (v1 implements the inbound side + the pull API; outbound webhook *delivery* is
  simulated/logged.)
- Suppliers mint **API tokens** for their org in the portal (simple org-scoped token
  model, hashed at rest); the seed's live-feed simulator uses them.

## 7. Roles & Permissions

| Role | Can |
| --- | --- |
| supplier | Manage own org profile/certs; submit EOIs; view own submissions/qualifications; bid on published RFPs where org holds a live matching-category qualification; view own contracts/shipments; push events via API tokens; submit portal check-ins; supplier ops view |
| reviewer | EOI review queue + per-category decisions; bid technical scoring |
| procurement_admin | Superset of reviewer + create/publish/close rounds and RFPs; registry browser; award lots; create contracts/appropriations/nodes; OES command view; resolve discrepancies; audit log |
| gov_observer | Read-only, country-scoped: flows touching their country, in-country dashboards |
| funder | Read-only, global: money-denominated views |

Enforced by a server-side `rbac.py` role × module × verb matrix; mirrored in a client
`perms.js` for show/hide only; locked together by a contract test that parses `perms.js`
(campaign's pattern). Cross-org isolation (suppliers see only their own org, API tokens
scoped to org) and country scoping (gov observers) enforced in every queryset and
covered by explicit tests.

## 8. Screens (one SPA, role-gated)

**Supplier portal:** dashboard (qualifications, open rounds, active RFPs, deadlines) ·
org profile + certifications editor · EOI wizard (categories → per-category commitments →
review + submit with snapshot) · my submissions · RFP list (qualified only) · bid
workspace (per-lot pricing table) · my bids & awards.

**Procurement console:** EOI round management · review queue → submission detail (frozen
snapshot beside per-category decision form) · **registry browser** (filter by
category/country/expiry) · RFP builder with lots · **bid comparison table per lot**
(price-ranked, technical scores inline) · award confirmation · contract + appropriation +
node management · API-token/feed status per supplier · audit log.

**The four visualization surfaces** (vocabulary from the viz research; every surface
carries an OCHA-style key-figures row and the unit ladder):

1. **Supplier ops view** — control-tower idiom scoped to my org: milestone rail per
   contract/shipment (production → QC → despatched → at port → delivered) with
   ETA-delta chips; production burn-up (cumulative cartons vs contracted); order table
   with exception chips; small map of *my* routes only (factory pins + arcs to assigned
   ports); check-in/delivery form.
2. **OES command view** — the hero screen, **exception-first**: prioritized at-risk
   queue left (each row: status → why → recommended action, sorted by severity ×
   tonnage), live map right — TripsLayer comet-trail shipments on **real routes** (sea
   lanes + road corridors), warehouse nodes sized by stock-on-hand, **IPC famine-phase
   choropleth underneath as the demand layer**; drill-in to per-shipment milestone
   timeline (planned/estimated/actual); WFP-style **pipeline table** (commodity ×
   corridor × month: requirement vs confirmed vs gap); dwell-time bars per node.
3. **Host-government view** — country-scoped: IPC choropleth (exact official colors) with
   flows into the country only; 5W-style table (which partner, what commodity, which
   admin-1, when, how many children); key-figures row (MT in-country, warehouses,
   children reached, coverage vs SAM caseload); stock-by-warehouse bars with
   months-of-supply coloring.
4. **US Government / ultimate-payer view** — money-first: **one conservative Sankey**
   (appropriation → contract/partner → country → delivered commodity, totals reconcile);
   **obligated vs disbursed vs delivered** three-stage bars per contract (never
   collapsed); unit-cost ladder tile ($ → MT → cartons → children treated, with
   methodology footnote); cost-per-child-treated trend; secondary global arc map with
   $-weighted widths. Aesthetic bar: screenshot-able for a congressional briefing.

Stretch (post-v1 polish): a Shipmap-style **narrated guided-tour mode** — scripted
camera path following one shipment Kano → Maiduguri as the demo opener.

## 9. Geo & Visualization Stack

- **Nodes** are lat/lng PostGIS points; **flows** render along real geometry: sea legs
  from searoute-computed lane paths (explicit Bab-el-Mandeb/Suez chokepoints — exactly
  our Port Sudan/Djibouti corridors), road legs from hand-digitized corridor polylines
  (Kano→Maiduguri, Djibouti→Addis→Gode, Lomé→Ouaga→Djibo, Port Sudan→El Fasher). All
  route geometry precomputed at seed time and stored on the Shipment — nothing computed
  live.
- Country/admin-1 outlines **and IPC phase classifications**: vendored static GeoJSON —
  deliberately *not* labs' `AdminBoundary` (campaign's #1 split blocker; our countries
  aren't loaded there anyway). IPC layer uses the exact official phase colors
  (`#CDFACD / #FAE61E / #E67800 / #C80000 / #640000`, white-hatch "Famine Likely") and
  IPC colors are reserved for IPC only; all other charts use a non-clashing palette.
- **Mapbox GL v3 (`dark-v11`) + deck.gl 9.3 pinned UMD via CDN**, integrated with
  `MapboxOverlay` in **interleaved** mode (flows under labels; IPC choropleth as a
  Mapbox fill layer so flows sit above it naturally). ArcLayer for strategic O/D views;
  TripsLayer for animated movement — static `data`, animate `currentTime` only via
  requestAnimationFrame; one deck instance per page, layer visibility toggles per
  dashboard mode; `pickable` only where tooltips are needed. **flowmap.gl skipped**
  (no UMD build; unneeded at our node count).

## 10. Seed — `seed_supply_demo`

Idempotent management command, fixed PRNG, deterministic output (campaign's seed
pattern + determinism test). Builds the entire demo world:

- ~16 supplier orgs — **fictional names, real-shaped** (RUTF makers in Kano, Lagos,
  Ouagadougou, Addis Ababa mirroring the real producer geography; none in Sudan;
  international transporters/freight forwarders; warehousing operators). Mixed quality:
  strong certs, expiring certs, thin profiles. GLNs/GTINs/SSCCs in demo-valid formats.
- Staff personas: OES procurement lead, reviewer, Nigeria gov observer, US funder — plus
  one supplier login. All with known demo passwords for live driving.
- One **closed** EOI round that populated the registry; one **open** round with
  submissions in every status (review-queue demo).
- One **published** RFP (3 lots, 4–6 bids each, partially scored); one **awarded** RFP as
  history.
- 2 appropriations (US Government FY envelopes with IATI-shaped activity IDs), 3
  contracts in execution with obligated/disbursed/delivered at different stages.
- ~30 supply nodes with GLNs: factories (Kano, Lagos, Addis, Ouagadougou), ports (Lagos,
  Port Sudan, Djibouti, Lomé), regional warehouses, distribution hubs in famine zones
  (Maiduguri, El Fasher, Gode, Djibo).
- ~40 shipments across all statuses with precomputed route geometry, ShipmentLines with
  GTIN/lot/expiry, planned/estimated/actual Milestones (some late → exception queue
  content), and several weeks of SupplyEvent history **spread across ingestion tiers**:
  the Kano factory emits clean EPCIS (including one TransformationEvent provenance
  chain), despatches arrive as ASN documents, the Port Sudan → El Fasher leg arrives as
  sparse check-ins, a couple of receiving discrepancies for the exception feed.
- Vendored IPC phase GeoJSON for the four countries (realistic point-in-time phases;
  famine-phase zones align with where distribution hubs sit, so flows visibly terminate
  in Phase 4/5 areas).

## 11. Testing Strategy

Campaign's `TESTING_STRATEGY.md` approach, ported:

- Self-contained `tests/` with factories; zero labs fixtures.
- RBAC contract test (`perms.js` ↔ `rbac.py` equality).
- Cross-org isolation, org-scoped API tokens, and gov-country scoping tests.
- Lifecycle tests: snapshot immutability after submit; can't bid without live matching
  qualification; can't award an unsubmitted bid; award → contract → appropriation
  linkage.
- Ingestion tests: EPCIS capture validation (accept the official example shapes),
  idempotency on external event ID (at-least-once), ASN → Shipment/Lines/Milestones
  materialization, events advance shipment status and stamp Milestone actuals,
  discrepancy raised on non-reconciling receipt, pull-API ≡ event log.
- Seed determinism + migration lockstep tests.
- Host-integration test: labs OAuth middleware must skip `/supply/`.
- Run against real Postgres/PostGIS locally (standard labs local test setup).

## 12. Satellite-Site Pattern (conventions this app follows)

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

## 13. Out of Scope (YAGNI)

- Real file uploads (certification documents are name stubs).
- Payments/invoicing, contract amendments, multi-currency conversion.
- Email/notifications; outbound webhook *delivery* (simulated/logged in v1).
- Raw X12/EDIFACT parsing, AS2 endpoints, GDSN master data, sachet-level serialization,
  FHIR, live route computation.
- Multi-org membership per user; org user management UI.
- Login-approval workflow (supplier registration is open; staff are seeded).
- Any shared "satellite framework" code, scaffolding, or extraction from labs.
- MCP tools for this app (can come later if authoring pain appears).

## 14. Implementation Phasing (for the plan)

1. **Procurement core** — app scaffold, auth pages, models/migrations, RBAC, org
   profiles, EOI rounds/submissions/review, registry, RFP/lots/bids/scoring/award, seed
   v1, tests.
2. **Supply execution + ingestion** — nodes, appropriations, contracts, shipments/lines,
   milestones, SupplyEvent log, the three-tier ingestion API + API tokens, discrepancies,
   supplier ops view, seed v2 (events, routes, IPC data).
3. **Visualization surfaces** — OES command view (exception queue + animated flow map),
   gov view, funder view (Sankey + three-stage bars); polish to the
   "congressional briefing" bar; stretch: narrated tour mode.
