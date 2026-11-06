# Supply: one relationship-based access model

Status: phase 1 (shadow mode) and phase 2 (read enforcement for partners, per programme, default OFF) have shipped. Phases 3 to 5 are not started.

## Why

Supply should let any organisation run its own programme, not just Dimagi (`2026-10-12-supply-running-organisation-design.md`), and
one organisation is routinely in several roles at once: running a procurement, bidding on another, receiving goods on a third.
What it may do with a row follows from how it is **related to that row**, not from a single standing for the whole programme.

Today access is programme-level only: `runner.standing()` decides programme | operator | partner once, and a partner who holds the
programme reads every row of it. Per-object scoping exists in exactly two hand-written places (`update_links.service._org_contracts`,
`market.service._own_quotes`). `?partner=` is a display filter (`partners/scope.py`), not a boundary. Partners can read quotes and
prices programme-wide (design 2026-10-12, H3).

## The model

`connect_labs/supply_chain/access_relations.py`.

- `relations(viewer_org_ids, row) -> set[Relation]`: derived **only** from fields the models already carry. No field is invented.
- `capabilities(relations) -> set[Capability]`: `read`, `record_own`, `answer_approval`. A runner holds all of them on everything in its programme.
- `scope(access, Model)`: the rows the caller's relationships reach. A caller with programme or operator standing (or the system caller)
  gets today's unfiltered programme queryset, unchanged. Everyone else gets the rows they hold a relation to. Fails closed: if the
  caller's organisations cannot be established, a partner gets nothing.
- `relations()` and `scope()` are built from the same lookup paths, and a test asserts a row is in a viewer's scope exactly when they hold a relation to it.

### Relations

| Relation | Derived from | Allows |
|---|---|---|
| `runner` | `ProgrammeRunner(program_id, org)` | everything, on everything in the programme |
| `tender_owner` | `Tender.owner_org` | read |
| `invited_bidder` | `Tender.invited_orgs` | read, record own |
| `supplier` | `Supplier.org` (and `Quote`/`Award`/`Contract` through it) | read, record own |
| `buyer` | `Contract.buyer_org` | read, record own |
| `receiver` | `Contract.delivery_supply_point.managed_by_org` | read, record own |
| `approver` | `AwardApproval.approver_org` | read, answer approval |
| `point_operator` | `SupplyPoint.managed_by_org` (and `Movement` through its two points) | read, record own |
| `payee` | `Charge.payee_org` | read |
| `owed_to` | `Commitment.owed_to_org` | read |

Modelled tables: Tender, Supplier, Quote, Award, AwardApproval, Contract, Shipment, Charge, Receipt, Invoice, Commitment, SupplyPoint, Movement.
Everything else (stock counts, distributions, plans, alerts, documents, ...) is not modelled yet and stays programme-level.

**Custodian is left out on purpose.** It has no organisation link; the only organisation near it is its point's `managed_by_org`, which is already `point_operator` on the
SupplyPoint. Deriving a custodian's organisation from that would invent a field.

### Decisions taken in phase 2 (owner, 5 Nov)

1. **Competing bidders' quotes are hidden from each other.** A bidder sees its own bids and activity only (quotes, its supplier row, its award, the outreach logged to it).
2. **The receiving partner of an order is a `receiver`.** `Contract.partner_org`, and the delivery point's `managed_by_org`, are `Relation.RECEIVER` on the order and on the shipments, receipts, charges and invoices that concern it (charges and invoices gained the relation; receipts and shipments already had the delivery-point path).
3. **A partner's field-worker points are the partner's.** A supply point below one a partner manages (any depth, via `parent`) is `point_operator` for it, wherever a relation is "manages the point": `SupplyPoint`, `Movement` (both ends), an order's delivery point, a receipt's point. The nearest managing ancestor wins (`stock.services.issues.partner_by_point`), so a worker under X's store that W manages is W's, not X's. Computed in Python from the programme's points (one pass over the tree), then `pk__in`.
4. **Aggregates, history/audit and exports are filtered per viewer in the NEXT phase.** Phase 2 adds only the relations and the scope support for them (below).

**`extended` keeps the update links where they were.** `rows_for(..., extended=True)` is the default and includes decisions 2 and 3. `update_links.service._org_contracts` and `market.service._own_quotes` pass `extended=False`, so what an update link or the marketplace reaches has not moved: widening what a link can record against is not decided here. `test_access_relations_phase2.py` pins `_org_contracts` equal to the original hand-written query with `partner_org` set and workers below stores, and shows the wider scope reaching rows the link does not. `Outreach` is now modelled (supplier and tender owner), because who a supplier was asked is a competitor's business too.

Still a gap by design: a public open tender is visible to any organisation on the marketplace; that is not a relation and stays in `market.service`.

## Phases

1. **Shadow (phase 1, shipped).** The module, the cross-org leak test harness, and shadow logging on reads. No behaviour change for any user. `_org_contracts` and `_own_quotes` are re-expressed through it, with tests running the original and new queries on the same fixtures.
2. **Enforce reads for partners** (this PR, below). Market and update links were re-expressed through the relations in phase 1 and keep their reach (`extended=False`); their bespoke queries are gone.
3. **Per-object write capabilities.** `record_own` / `answer_approval` checked per row, replacing "writes need programme standing, partners use links".
4. **Close H3, the rest.** Phase 2 decided competing bidders are hidden from each other and made it real per programme behind the switch. What remains for H3: aggregates, history/audit and exports filtered per viewer (see the list above), then flipping the default to on once every programme has been previewed.
5. **`?partner=` derived from the viewer**, not a query parameter.

## Read enforcement (phase 2)

**The switch.** `acl_enforce_reads` in the programme's Supply settings (the `supply` namespace, the same store as `required_supplier_checks` and `purchase_packs_per_child`; no migration). Default off. Read from the **programme layer only**: an organisation-layer value is ignored, so one setting cannot switch on every programme an organisation owns. Only a literal JSON `true` turns it on.

- Turn on / off, over MCP: `labs_config_set` with `namespace: "supply"`, `scope: program`, the programme id, and `acl_enforce_reads: true` (or `false` / remove the key). In the UI: Settings at `/labs/settings/program/<programme id>/` (the Supply section). Whoever may change that programme's Supply settings may flip it (its members and Dimagi staff, per `scope_config/service.py`); the change is attributed and undoable like any setting.
- Preview first: `supply_chain_access_preview` with `org_id` (the LABS organisation id from `org_list`), run as the programme's runner or platform support, answers per table how many rows that organisation would see against the programme total, and whether the switch is on. The preview uses the same `scope_for_orgs` as the enforcement and does not depend on the switch. A partner is refused it. Python: `access_preview.preview(program_id, org_id)`.

**What is enforced, and for whom.** For a caller with **partner** standing (`runner.standing`), the repository reads of an operation's own table (`SupplyDataAccess.list_*` / `get_*`) are restricted to `access_relations.scope(access, Model)` while the operation runs. The doors are `operations.call_operation` for the 20 read operations in `access_shadow.READ_OPERATION_MODELS` (the 19 phase 1 observed plus `outreach_list`), which is the one door the generated MCP tools and the page views share, and the two direct reads in `views.py` (`_tender_replies`, `_link_updates`). Programme, operator and system callers get the very same queryset object (`access_enforce.restrict` returns it untouched; asserted on identity and SQL). Fail closed: a partner whose organisations cannot be established, or whose standing cannot be read, gets nothing. A switch that cannot be read is treated as off.

**Only the operation's own table is scoped.** The tables a handler looks up on the way (a quote's tender, a movement's point) are not, so scoping a list can never make a handler dereference a row it was not allowed to see. The cost: a record that embeds another table's data carries that data (e.g. `tender_get` returns the tender's own lines; a quote's derived figures use its tender).

**Pages.** Pages read through `call_operation`, so they inherit it; a partner's page says so in a note under the header ("You are seeing the part of this programme you are involved in...") and an empty list says "You have no tenders / orders / suppliers / supply points / stock movements in this programme." (`acl_scoped` in the page context). The tender board's own `sourcing_rows` are filtered to the tenders the partner may list, and the purchase guide (the runner's programme-wide checklist, which names every tender) is not shown to a partner under enforcement. Render tests (`test_access_enforce_pages.py`) draw twelve main pages for a partner and an unrelated organisation with enforcement on and off, in two programmes, and assert they degrade rather than fail; `?partner=<another org>` still shows a partner only its own.

**The shadow keeps running** for programmes that have not switched on. When a read is enforced for real the shadow stays quiet (it would only count rows already hidden); it still observes any read it knows that is not enforced.

### Still NOT filtered (phase 2 does not change any of this)

- **Aggregates and derived figures.** Stock position / on hand / by batch / flow / forecast, network stock and tree, landed cost, comparison, supplier performance, issue summary, order-set progress, purchase sizing, resupply plan, checks, overview figures, the map, the Stock page's points. Listed as `PHASE_4_AGGREGATES` in `test_access_relations_mcp_coverage.py`; the Stock page is pinned as a known gap in `test_access_enforce_pages.py`.
- **History and audit.** `OperationCall` / `Revision`, the contract timeline (`timeline_for_contract`), `?as_of=` rewinds.
- **Exports.**
- **Writes.** Still "writes need programme standing, partners use links"; per-object write capabilities are phase 3. All writes are still UNCOVERED in the ratchet.
- **Other reads of unmodelled tables** (stock counts, issues, distributions, plans, alerts, documents, custodians, people, dispensing rules, catalogue) stay programme-level.
- **Nested data inside a record of an enforced table** that comes from another table (above).
- **Direct model queries in services and detail pages** (`sourcing_rows` aside, e.g. an award page's `Award.objects.filter(pk=...)` before its operations run) are guarded by the programme id, not by relations.

Ratchet after phase 2: **20 enforced tools, 1 runner-only (`access_preview`), 127 uncovered** (of 148); 14 aggregates among the uncovered are flagged for phase 4.

## Shadow mode (phase 1)

`access_shadow.observe_read`, called from `call_operation` after any read operation has returned its result. For a partner-standing caller, on one read in
`SUPPLY_ACL_SHADOW_SAMPLE` (default 20), it counts the rows `scope()` would have hidden and logs one INFO line:
`supply acl shadow: op=quote_list model=Quote standing=partner would_hide=7 program=20996`. Flag: `SUPPLY_ACL_SHADOW` (default on, logging only). It never changes
a result and never raises. Cost when on and sampled: one COUNT plus the standing and organisation lookups the request already caches; flag off costs nothing.
Only the operations in `READ_OPERATION_MODELS` (reads whose rows are one modelled table) are observed. Page views that read through the repository directly are not yet observed.

## Risks

- **MCP tools are programme-wide.** Every generated tool takes a `program_id` and, for a partner, reads the whole programme. 128 of 147 tools are not covered by any scope test yet
  (`test_access_relations_mcp_coverage.py` lists them; a new tool fails the ratchet until decided).
- **Exports and history.** The timeline, `OperationCall` / `Revision` history, and any export carry rows by programme, not by relation. Scoping the tables without scoping history leaks the same rows through it.
- **Aggregates leak totals.** Stock position, landed cost, comparison and overview figures sum across rows the viewer may not see. Scoping rows does not scope totals; each needs a decision.
- **Caches keyed by programme.** Anything cached per programme would serve one viewer's scope to another. Keys need the viewer's relations (or must stay runner-only).
- **Viewer resolution costs a Connect lookup.** `viewer_org_ids` reuses `identity`, so it inherits its TTL cache and its failure mode (unknowable means nothing, never everything).

## A partner with no programme-level read

A partner who holds no programme-level read has no access today and works through update links (`update_links/`), which already scope to the organisation's own orders and stores.
Nothing in this design gives such a partner anything: relations scope what a *reader* sees; they do not grant the right to open the programme.
