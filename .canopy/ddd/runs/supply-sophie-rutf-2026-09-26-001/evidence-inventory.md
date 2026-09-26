# Evidence Inventory — supply-sophie-rutf
Generated: 2026-09-26T15:26:48+00:00

## Summary
- documented: 16 items
- implemented: 14 items
- assumed: 2 items
- Total: 32 items

Real-data rule: this repo is public. Figures, supplier names and dates of the real rounds live in the Drive document the seeder reads; none are copied here.

## Items

### [EV-001] The problem: the supply record has no memory.
- **kind:** documented
- **ref:** docs/superpowers/specs/2026-09-26-supply-sophie-history-design.md §1
- **summary:** Supply records are overwritten in place, so an ETA slip from 5 Sep to 19 Sep erases the earlier belief and nobody can ask what was believed on 20 Aug or who changed it.
- **claim_hint:** The problem: the supply record has no memory.

### [EV-002] Persona and channel: Sophie alone, AI does the typing.
- **kind:** documented
- **ref:** docs/superpowers/specs/2026-09-26-supply-sophie-history-design.md §1
- **summary:** Sophie, program manager for Connect-RUTF (program 263, org dimagi-ng-rutf), is the only person inside the system; supplier and forwarder email reaches it through an external AI (ACE, Claude over MCP, or the canopy panel) writing through the same operations.
- **claim_hint:** Persona and channel: Sophie alone, AI does the typing.

### [EV-003] Every change is recorded with who and on what evidence.
- **kind:** documented
- **ref:** docs/superpowers/specs/2026-09-26-supply-sophie-history-design.md §3.1-3.3
- **summary:** An append-only Revision row per change (actor, channel, operation, optional source ref + excerpt, recorded_at) is written from call_operation's write context; a repeated source.ref for the same operation is replayed, not re-written.
- **claim_hint:** Every change is recorded with who and on what evidence.

### [EV-004] AI-entered records are badged with their source.
- **kind:** documented
- **ref:** docs/superpowers/specs/2026-09-26-supply-sophie-history-design.md §3.4
- **summary:** Revisions written over mcp/api read 'via AI · Sophie' or 'ACE (agent)', web writes read 'Sophie', and the source excerpt opens from the badge.
- **claim_hint:** AI-entered records are badged with their source.

### [EV-005] Sophie can see the state on any past date.
- **kind:** documented
- **ref:** docs/superpowers/specs/2026-09-26-supply-sophie-history-design.md §3.5, §4.4
- **summary:** ?as_of=YYYY-MM-DD renders any program-scoped supply page as it stood at end of that day (rolled-back rewind), with a persistent banner, write controls hidden and writes refused; the market ignores as_of.
- **claim_hint:** Sophie can see the state on any past date.

### [EV-006] Sophie's program-scoped overview with stale flags.
- **kind:** documented
- **ref:** docs/superpowers/specs/2026-09-26-supply-sophie-history-design.md §4.1-4.2
- **summary:** The overview names 'Connect-RUTF · Dimagi, buyer of record', lists only this program's tenders and orders with stage, waiting-on, last change (when/who), a stale flag (outreach unanswered ≥14 days, expected_on passed without receipt, a basis not_specified the comparison needs) and an AI badge.
- **claim_hint:** Sophie's program-scoped overview with stale flags.

### [EV-007] Per-record timeline; correct an AI-entered quote.
- **kind:** documented
- **ref:** docs/superpowers/specs/2026-09-26-supply-sophie-history-design.md §4.3
- **summary:** Tender/order detail keeps current state on top and a newest-first plain-language timeline of the record and its children ('ETA 5 Sep → 19 Sep'), each line badged, excerpts openable, AI-entered quote lines offering Correct and Void.
- **claim_hint:** Per-record timeline; correct an AI-entered quote.

### [EV-008] The market shows only public facts.
- **kind:** documented
- **ref:** docs/superpowers/specs/2026-09-26-supply-sophie-history-design.md §4.5
- **summary:** /supply/market/ stays a live cross-buyer listing of public tenders showing only what a tender publishes: no revisions, excerpts, other suppliers' quotes or as-of.
- **claim_hint:** The market shows only public facts.

### [EV-009] Demo data exists for every scene.
- **kind:** documented
- **ref:** docs/superpowers/specs/2026-09-26-supply-sophie-history-design.md §5
- **summary:** The demo runs on synthetic program 10672 mirroring 263; the seeder replays round 1 as dated history (some steps as ACE over mcp, some as Sophie over web, one replayed twice), seeds round 2 open with three incomparable quotes and five non-responders, and seeds other synthetic buyers' public tenders; facts come from Drive, never the repo.
- **claim_hint:** Demo data exists for every scene.

### [EV-010] The story arc is owner-agreed.
- **kind:** documented
- **ref:** docs/superpowers/specs/2026-09-26-supply-sophie-history-design.md §6 + status line
- **summary:** The six-scene narrative (overview today → round-1 timeline → as of 20 Aug → round-2 comparison + correction → market → award with rationale) was agreed with the owner in conversation on 2026-09-26.
- **claim_hint:** The story arc is owner-agreed.

### [EV-011] Sophie's real workflow and why history/provenance matter.
- **kind:** documented
- **ref:** docs/superpowers/specs/2026-09-11-rutf-procurement-design.md §0-§1
- **summary:** RUTF buying ran in a spreadsheet; the three round-2 quotes cannot be compared (per carton without sachets per carton, per sachet with freight unstated, duties excluded); suppliers mostly do not reply; quotes are expected to arrive by forwarding the supplier's email to an agent.
- **claim_hint:** Sophie's real workflow and why history/provenance matter.

### [EV-012] Missing-basis flags are honest, not errors; AI guesses need correcting.
- **kind:** documented
- **ref:** docs/superpowers/specs/2026-09-11-rutf-procurement-design.md §5.6
- **summary:** Every quote basis flag (freight, duties, pack spec) defaults to not_specified because a plausible guess is the commonest failure for a person in a hurry and for a model reading an invoice.
- **claim_hint:** Missing-basis flags are honest, not errors; AI guesses need correcting.

### [EV-013] Correcting an AI-entered quote keeps the old version.
- **kind:** documented
- **ref:** docs/superpowers/specs/2026-09-11-rutf-procurement-design.md §11
- **summary:** Quotes are append-only: quote_correct writes a new version with supersedes and a reason; quote_void leaves the row readable and out of comparisons.
- **claim_hint:** Correcting an AI-entered quote keeps the old version.

### [EV-014] Award with rationale.
- **kind:** documented
- **ref:** docs/superpowers/specs/2026-09-11-rutf-procurement-design.md §5.7
- **summary:** An award requires a rationale and freezes the comparison as it stood at the moment of decision.
- **claim_hint:** Award with rationale.

### [EV-015] Buyer-of-record framing (tension with 'Dimagi, buyer of record' header).
- **kind:** documented
- **ref:** docs/superpowers/specs/2026-09-11-rutf-procurement-design.md §17
- **summary:** buyer_of_record is a property of the contract; in the real round 1 the local partner placed the order to claim local duty relief.
- **claim_hint:** Buyer-of-record framing (tension with 'Dimagi, buyer of record' header).

### [EV-016] A single place to capture revisions exists.
- **kind:** implemented
- **ref:** connect_labs/supply_chain/operations.py:119 call_operation + identity.stamp_provenance
- **summary:** Every web, HTTP and MCP write passes one choke point that validates the schema and stamps provenance before dispatch.
- **claim_hint:** A single place to capture revisions exists.

### [EV-017] The AI channel already works.
- **kind:** implemented
- **ref:** connect_labs/supply_chain/mcp_tools.py
- **summary:** Each supply operation is exposed as a supply_chain_* MCP tool, so an external AI writes through the same operations a person does.
- **claim_hint:** The AI channel already works.

### [EV-018] Quote fields for missing basis and correction exist.
- **kind:** implemented
- **ref:** connect_labs/supply_chain/models.py Quote
- **summary:** Quote stores as-quoted amount/unit, freight/duties/pack basis defaulting to not_specified, voided/void_reason, version/superseded_by/correction_reason, entered_by and entered_by_user.
- **claim_hint:** Quote fields for missing basis and correction exist.

### [EV-019] Correct/Void targets for the timeline exist.
- **kind:** implemented
- **ref:** connect_labs/supply_chain/urls.py procurement_quote_correct / procurement_quote_void
- **summary:** Correct and Void screens exist for a quote (QuoteCorrectView, QuoteVoidView).
- **claim_hint:** Correct/Void targets for the timeline exist.

### [EV-020] Round-2 comparison flags missing bases.
- **kind:** implemented
- **ref:** connect_labs/supply_chain/procurement/services/comparison.py + checks._sourcing
- **summary:** compare_tender splits quotes into comparable (ranked) and blocked, each blocked row carrying the questions that would unblock it and who can answer.
- **claim_hint:** Round-2 comparison flags missing bases.

### [EV-021] Award with rationale is filmable today.
- **kind:** implemented
- **ref:** connect_labs/supply_chain/urls.py procurement_comparison, award_detail
- **summary:** Award is made from the comparison with a typed rationale and opens an award page (as filmed in supply-test-kits).
- **claim_hint:** Award with rationale is filmable today.

### [EV-022] ETA and lateness are data today; only the earlier ETA is lost.
- **kind:** implemented
- **ref:** connect_labs/supply_chain/models.py Shipment; checks._late_shipments
- **summary:** Shipment has dispatched_on, expected_on, status; a shipment past expected_on raises shipment_overdue.
- **claim_hint:** ETA and lateness are data today; only the earlier ETA is lost.

### [EV-023] Non-responders are countable; the stale flag re-introduces a threshold.
- **kind:** implemented
- **ref:** connect_labs/supply_chain/models.py Outreach; checks._sourcing docstring
- **summary:** Outreach holds sent_on/responded/response_kind; a 'has not replied' check was deliberately removed as a judgement about silence.
- **claim_hint:** Non-responders are countable; the stale flag re-introduces a threshold.

### [EV-024] Overview exists but not in the §4.2 shape.
- **kind:** implemented
- **ref:** connect_labs/supply_chain/views.py DomainHomeView
- **summary:** /supply/ requires a program context and shows a stage funnel, checks grouped by audience, tenders and contracts; there is no last-change, actor, AI badge or stale column.
- **claim_hint:** Overview exists but not in the §4.2 shape.

### [EV-025] Global market with public facts exists.
- **kind:** implemented
- **ref:** connect_labs/supply_chain/market/service.py visible_tender/listed_tenders
- **summary:** The market lists open tenders visible to the visitor across buyers and shows a visitor only its own quotes.
- **claim_hint:** Global market with public facts exists.

### [EV-026] Base demo world exists; dated history replay does not.
- **kind:** implemented
- **ref:** scripts/walkthroughs/oes-demo/seed_remote.py seed_rutf_rounds
- **summary:** Program 10672 seeds round 1 through seed_chain (quotes, award, contract) and round 2 open with three suppliers each incomparable for one reason, no award; facts read from the Drive document.
- **claim_hint:** Base demo world exists; dated history replay does not.

### [EV-027] Scenes 1-4 depend on unbuilt capability.
- **kind:** implemented
- **ref:** grep of connect_labs/supply_chain (2026-09-26)
- **summary:** No Revision model, source_excerpt, recorded_at write context, request-level as_of, timeline or AI badge exists in supply_chain today (as_of there is a stock-ledger date filter only).
- **claim_hint:** Scenes 1-4 depend on unbuilt capability.

### [EV-028] Dimagi as buyer of record is the owner's chosen framing for this narrative.
- **kind:** documented
- **ref:** .canopy/ddd/learnings.md 2026-09-24 [supply/DECISION NEEDED]
- **summary:** The seeder picks the real 'dimagi' org as buyer of record, which put a real org in an otherwise invented cast; surfaced, not decided.
- **claim_hint:** Dimagi as buyer of record is the owner's chosen framing for this narrative.

### [EV-029] Scene 2/3 dates.
- **kind:** assumed
- **ref:** design spec §1 example
- **summary:** The specific ETA slip (5 Sep → 19 Sep) and the 20 Aug as-of date are illustrative dates, not verified against round 1's real shipment record.
- **claim_hint:** Scene 2/3 dates.

### [EV-030] Scene 5 market context.
- **kind:** assumed
- **ref:** design spec §5
- **summary:** Other buyers' public RUTF tenders on the market are synthetic and illustrative; no real other buyer is claimed.
- **claim_hint:** Scene 5 market context.

### [EV-031] An award made while other quotes are still blocked is honestly provisional.
- **kind:** implemented
- **ref:** connect_labs/supply_chain/procurement/services/comparison.py:503-546
- **summary:** A ranking is provisional whenever any quote was blocked, and an award made from it records that flag and the frozen comparison.
- **claim_hint:** An award made while other quotes are still blocked is honestly provisional.

### [EV-032] The question to each supplier is already written out; a comparable quote shows cost per child.
- **kind:** implemented
- **ref:** connect_labs/templates/supply_chain/procurement/_row_questions.html; procurement/services/pricing.py usd_per_child_treated; followup_render operation
- **summary:** Each comparison row lists the questions to ask the supplier in words, a ranked quote carries USD per child treated, and followup_render returns a drafted follow-up for a client (the drafted-email page itself was removed from the product on purpose, design §22).
- **claim_hint:** The question to each supplier is already written out; a comparable quote shows cost per child.
