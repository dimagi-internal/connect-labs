# OES supply — product-defect backlog

> **Status, 31 July 2026 (third update — the three parked narrative forks are
> RESOLVED, and every PRODUCT finding from the second judge pass is closed).**
>
> Jon lifted the concept_change gate ("I don't need to review any concept changes
> that you think are better, just make them"), so the three forks the previous
> pass parked are now decided and built:
>
> - **supply-base scene 6 — publish on camera.** Resolved by publishing a
>   DIFFERENT tender. Ada creates, lots and publishes the Q4 call on camera;
>   scenes 7-8 go on awarding the mature Q3 tender, which needs bid history a
>   fresh publication cannot have. Narration rewritten; scene 7 now names the
>   switch out loud. The supplier-side ABSENCE beat is still not filmed (it needs
>   a mid-scene persona switch via dev-login, a hard 403 on prod) — instead
>   eligibility is shown from the publisher's side as a positive artifact, the new
>   qualified-supplier count.
> - **partner-pipeline scene 2 — narrated examples vs the seeded world.**
>   Resolved toward the WORLD, not the script: the seeded grid was measured and
>   the narration rewritten to the cells it actually renders (Monguno covered on
>   900 against 548 booked; Askira amber at 38 with 94 arriving *after*; Biu
>   uncovered). The rendered world turned out to teach the subtler point better
>   than the script did — an amber cell with a lorry on the way, where adding the
>   two figures gives the opposite of the truth.
> - **partner-pipeline scene 4 — record the count on camera.** The seeder no
>   longer pre-creates the discrepancy. `SHP-2026-0930` is seeded delivered and
>   UNCOUNTED, the storekeeper records 840 against its 900-carton advice on
>   camera, and the app's own `_reconcile_receipt` raises the 60-short
>   discrepancy in front of the viewer.
> - **money-to-child scene 2 — the fourth stage and the unattributable band.**
>   Resolved by BUILDING them rather than re-narrating: the Sankey is now four
>   columns (appropriation → contract → country → commodity), value that cannot
>   be hung on an envelope is drawn as its own grey band instead of being
>   excluded in a footnote, and middle-column labels carry a white halo over the
>   ribbon channel.
>
> **Making those acts real flushed out three more latent product bugs** — the
> same pattern as the previous cycle, and the reason act-fidelity is worth the
> effort:
>
> - **A receiving partner could not record a receipt at their own site.** Every
>   execution write endpoint scoped shipments on `contract__org`, so "Record the
>   count" on the partner's own Receiving screen returned a flat 404 and the
>   inversion that surface exists for could not be performed. Receipt authority
>   now follows NODE OWNERSHIP (`_reportable_shipment` /
>   `ingestion.assert_may_report`), the same rule the partner's site list and
>   cover figures are scoped by.
> - **A short receipt banked the ADVISED quantity, not the counted one.**
>   `cover._event_cartons` recognised `cartons` and `EA` but not `CT` — which is
>   what the EPCIS path, the hand-keyed webform and the execution seeder all
>   write — so a CT row summed to zero and the zero then fell back to
>   `shipment.quantity`. A site that received 840 against a 900 advice reported
>   **900 on hand**, contradicting the discrepancy raised from the very same
>   event, on the one screen whose thesis is that the count beside the pallets is
>   the figure of record. The execution seeder's own short leg was mis-banked the
>   same way, so command-centre stock figures were wrong too.
> - **A partner could read any consignment in the network by id.** Only the
>   `supplier` role was scoped in `shipment_detail`; `partner` was not.
>
> Plus the qualification-term drift the backlog had listed as an open `options`
> item: the term is now **18 calendar months** on both sides (it was 540 days on
> the server and a separate 540-day preview in the browser, both described on
> screen as "18 months"), and a pass granted off a certificate that lapses first
> carries a `verify_at` re-verification date instead of silently outliving its own
> evidence.
>
> `pytest connect_labs/supply` is at **252 passed**, up from 246, with 8 new
> regression tests (the CT/short-receipt trio, the on-camera receipt trio, and the
> calendar-month + re-verification pair).
>
> **Nothing is parked for the gate any more.**

> **Status, 31 July 2026 (second update — post act-fidelity cycle).** Since the
> banner below was written, four more product PRs landed and deployed:
>
> - **#1093 + #1096** — sticky table headers never rode the scroll: `.table-wrap`'s
>   `overflow-x: auto` made the wrap the sticky scrollport, so the topbar offset
>   parked every header row 54px INTO its table, over row one, eating clicks
>   (an Open-round and an Award button among them). Offset removed; the original
>   sticky-header finding is REOPENED below (see the money-to-child scene 4 entry).
> - **#1095** — creating a round/tender/lot with dates 500ed on its own success
>   response (raw ISO strings on the in-memory instance broke `.isoformat()`),
>   silently stacking orphan rows per retry.
> - **#1097** — a renewed certificate won the frozen-vs-live panel's live column
>   by coin flip (`cert_type`-only ordering left same-type ties in undefined
>   database order).
> - **#1094** — the seeder no longer pre-creates the open EOI round, its
>   submissions, or the post-snapshot certificate renewal: the walkthrough now
>   performs those acts on camera (Ada declares and opens 2026-B, Amina applies
>   and renews, Tomas decides), which is what surfaced #1095 and #1097.
>
> A fresh dual-judge pass over all four re-rendered arcs (runs
> `oes-*-2026-07-31-*`, verdicts + 52 routed findings in each run dir's
> `verdict-concept.yaml` / `design_findings.json`) produced a new tranche of
> PRODUCT findings, appended at the end of this file, and three narrative-level
> forks that are deliberately parked for the concept_change gate (supply-base
> scene 6's publish act; partner-pipeline scene 2's narrated examples vs the
> seeded calendar and scene 4's record-the-count act; money-to-child scene 2's
> narrated fourth stage / unattributable band).

> **Status, 31 July 2026.** Seven batches (PRs #1081, #1082, #1084, #1085, #1086,
> #1087, #1088) closed the substantive product findings below. **49 fixes were
> verified against the deployed app** at `labs.connect.dimagi.com` after a
> programmatic reseed, with zero console errors; `pytest connect_labs/supply` is
> at **238 passed**, up from 228, with 11 new regression tests.
>
> What is INTENTIONALLY still open here:
>
> - **Demo-craft findings**, which read as product findings in this file but are
>   not. Anything tagged `act-claimed-not-performed` is a *recipe* defect — the
>   narration describes an act the action_trace never performs — and is fixed by
>   rescripting a scene, not by changing `/supply/`. Same for
>   `canonical-frame-is-the-aftermath` and the cursor-occlusion notes.
> - **Layout/visual-hierarchy findings** that are a designer's taste call rather
>   than a defect: flat table typography, empty lower thirds, rail heights, two
>   button styles in one column. Several were partly addressed where they
>   coincided with a correctness problem; the rest want a human's eye.
> - **One genuinely forked decision**: whether coverage should MEAN reached-a-child
>   rather than supply-positioned. #1081 renamed the metric and added the missing
>   companion figure — the least destructive honest option, moving no existing
>   figure — rather than changing the numerator, which would silently invalidate
>   figures three narratives quote out loud. See that PR's description.
>
> **Verify before fixing.** Several entries below were already fixed when this
> file was written, and three more were fixed while working it. Check the finding
> against current code AND a fresh render first — §AG of the narrative-set review
> is the cautionary example, having listed four defects as open when three were
> already closed.

Every PRODUCT finding the three judge harvests produced, deduplicated on
(arc, scene, dimension, detail). **Narrative-independent**: each of these is true
for any user of
`/supply/`, regardless of what a demo says about it. Demo-craft and narrative
findings are deliberately NOT here — they need a human's taste and live in the
run dirs.

## How to read this

`docs/walkthroughs/oes-narrative-set-review.md` is the cautionary example: its
§AG, §AF and §H sections each listed defects as open that had been fixed commits
earlier, and re-verifying them cost real time twice in one session. So:

- **Verify before fixing.** Check the finding against current code AND against a
  fresh render. Three of the four §AG defects were already fixed; one §AF finding
  (registry rows "not navigable") was simply wrong — a judge scores one still
  frame and cannot press anything.
- `fix_kind: mechanical` means the judge named exactly one concrete change.
  `options` means it forked and needs a decision.
- Entries marked **FIXED** landed in this session; left in place so nobody
  re-derives them.

Harvested from: oes-command-centre-2026-07-30-001, oes-money-to-child-2026-07-30-001, oes-supply-base-2026-07-29-002

**84 deduplicated product findings** (mechanical 54, options 30, redesign 0) from 84 raw.

## oes-command-centre

### scene 2 · visual_polish · mechanical

The contract modal renders with no backdrop scrim, so the dashboard beneath it bleeds through in half-legible fragments that frame the load-bearing content: the left nav truncated to 'Supplier regis' and 'Command ce', orphaned numbers '30,000 / 14,000 / 36,000 / 26,000' down the right side, a clipped 'AP' where 'GAP' is cut off, a stray '(N)', and along the bottom edge a half-covered coverage row reading 'Somali IPC 4 · 29,576 · 20,000 · 67.6% · 9,576'. Two unrelated datasets compete in one frame, and the unrelated one includes numbers ('26,000', '14,000') that also appear in the modal's own footer, inviting a misread.

**Proposed fix:** Add a dimmed backdrop overlay behind the modal (a semi-opaque scrim over the page, e.g. rgba(0,0,0,0.45)) so nothing from the underlying dashboard is legible while a contract is open.

### scene 4 · design_soundness · mechanical

Internally impossible milestone arithmetic, sitting in the frame that claims figures cannot be massaged: 'Arrive · Kassala Forward Store — PLANNED Jul 26, 2026 / ESTIMATED Jul 26, 2026 / ACTUAL — / 0d vs plan' is displayed directly above 'Arrive · El Fasher Distribution Hub — PLANNED Jul 30, 2026 / ESTIMATED Aug 8, 2026 / +9d vs plan'. The consignment cannot be nine days late downstream while exactly on plan at an intermediate node whose arrival has never been recorded and whose planned date has already passed. Compounding it, the same '0d vs plan' chip denotes a MEASURED variance on the Depart row (ACTUAL present) and a FORECAST variance on both rows where ACTUAL is '—', with no visual dist

**Proposed fix:** Propagate the downstream delay through unreported upstream milestones (or render an unreported milestone past its planned date as 'overdue · unreported' rather than '0d vs plan'), and label the variance chip by what it was computed against — 'actual vs plan' versus 'estimate vs plan' — so a measured figure is never presented in the same typography as a forecast.

### scene 4 · visual_polish · mechanical

The modal's sticky header occludes scrolled content with no mask or scroll-margin: at the top of the scroll area 'Arrive · Kassala Forward Store' is rendered as a half-height sliver of horizontally-cut glyphs, and its PLANNED/ESTIMATED/ACTUAL row is left dangling under a decapitated label. Secondary issues in the same frame: 'Record an event' and 'Close' carry identical weight and outline so the write action has no primary treatment; the sole log row has a blank 'QUANTITY —'; and the underlying map bleeds past the modal edge with a half-cut legend chip reading 'od insecurity'.

**Proposed fix:** Give the modal's scroll container a top scroll-margin/padding equal to the sticky header height plus a gradient mask so no row is ever sliced mid-glyph, and promote 'Record an event' to a filled primary button against a plain 'Close'.

### scene 7 · design_soundness · mechanical

The partner-raised origin marker names the organisation but not when it was reported. The card reads 'Partner shortfall / Raised by Komadugu Health Initiative / 87 children at Askira Nutrition Centre by 6 August' — no report date on the card and none anywhere in the captured page text. The declared feature promises a marker 'naming the organisation, site and report date', and the scene's own ai_quality claim is 'The partner-raised marker names the organisation and the report date'. The date is the half that makes the marker a provenance stamp rather than a label; without it a reader cannot tell whether the partner reported this today or last month.

**Proposed fix:** Render the report date inside the origin pill on ShortfallSignal rows raised by a partner — e.g. 'Raised by Komadugu Health Initiative · 26 Jul 2026 (4 days ago)' — and extend test_a_partner_raised_row_says_so_and_a_derived_one_does_not to assert the date renders.

### scene 1 · design_soundness · options

Three interaction-coherence gaps a first-time user hits on the first card. (1) The recommended action reads "→ Expedite the consignment, or reallocate from a node holding surplus." but the only controls are "Open SHP-2026-0202" and "Reallocate to El Fasher Distribution Hub" — half the recommendation has no affordance anywhere on the screen. (2) The state-changing "Reallocate to El Fasher Distribution Hub" is the solid-green primary while the read-only "Open SHP-2026-0202" is the outline secondary, so the loudest control on the screen commits stock movement. (3) The "i" disclosure bubble is on "Children at risk" only; "In transit", "Delivered to date" and "Active contracts" have no way to rev

**Proposed fix:** Either add the missing controls so every recommended verb has an affordance (an "Expedite" action beside "Reallocate") and demote the committing action to secondary with "Open" as primary — or, if expedite is out of scope, stop recommending it in the prose and say only what the row can actually do. Add the "i" bubble to all four KPI tiles either way.

### scene 1 · visual_polish · options

Four product-presentation defects, two of which hard-cap this dimension. (1) Editorializing colour: "2,428" is red with a red left accent bar and no threshold stated anywhere on the frame, so the colour tells the reader the conclusion instead of encoding an objective fact. (2) Density: the ranked queue is the scene's load-bearing artifact and only one complete card plus a severed second one fit a normal viewport — "15,000 cartons despatched against 14,760 counted at" is cut by the fold — so ordering is not perceivable. (3) The map shows ~25 markers in at least six fills (blue, orange, crimson, green, teal, pink) with no node-colour legend in frame and a ~12-marker fully-occluded clump over B

**Proposed fix:** Options, pick per item: for (1) either drop the red or bind it to a stated threshold shown in the "i" bubble; for (2) give the queue a compact row form (one line per exception: severity chip, children, node, days late, action) so 6-8 ranked rows and their order are visible in one viewport, with the prose detail on expand — do NOT fix this by cropping or zooming the capture; for (3) add a persistent node-colour legend inside the Network panel, cluster overlapping markers with counts, and either m

### scene 2 · design_soundness · options

The modal's arithmetic does not hold, and money depends on it. Row quantities sum to 6,388 + 11,000 + 9,000 + 14,000 = 40,388 cartons against a footer reading '40,000 cartons contracted' — the 388-carton overshoot is the 6,388 that arrived at Khartoum on SHP-2026-0201 (14,000) and then moved onward to Kassala on SHP-2026-0204 (6,388), counted twice because sequential legs are summed flat against one contract quantity. Separately, '14,000 confirmed at destination' counts SHP-2026-0201, whose STATUS is only 'Confirmed' and whose destination is 'Khartoum Central Warehouse' (an intermediate warehouse), while EXCLUDING SHP-2026-0204 (6,388) whose status is 'Delivered' and which actually reached a

**Proposed fix:** Fix the model, not the label. Options: (a) nest onward-distribution legs under the primary haulage movement so a carton can only be counted once against a contract quantity, and show the schedule as a two-level tree; or (b) keep the flat list but compute the footer from distinct cartons and add a 'leg type' column (primary haulage / onward distribution) so the reader can see why the rows over-sum; and in either case define the state machine on screen — make 'Confirmed' and 'Delivered' ordered st

### scene 2 · design_soundness · options

The load-bearing vocabulary has no definitions available on demand, and the one explanatory sentence contradicts the table. 'REPORTED BY / Phone check-in' has no tooltip or 'i' bubble anywhere in the modal, and the header reads as WHO reported rather than HOW the event was captured or how much to trust it; 'Confirmed', 'Delivered' and 'In transit' are equally undefined. Directly beneath four 'Phone check-in' rows the footer asserts 'Status and quantities are derived from the event log, not entered.' — but those events WERE entered by hand; only the derivation is automatic. And the single 'DUE' column contradicts the lateness the same product reports elsewhere: SHP-2026-0202 shows 'DUE Jul 30

**Proposed fix:** Add on-demand definitions rather than more prose: an 'i' bubble on the REPORTED BY header defining the three ingestion tiers and what each implies about confidence, and one on each status pill defining the state. Rewrite the footer sentence to say what is true — e.g. 'Status is derived from the event log; it cannot be edited directly. Individual events may be hand-keyed, and are marked where they are.' And replace the single DUE column with the planned/estimated/actual triple the product already

### scene 3 · design_soundness · options

Three interaction/model incoherences in one card. (1) 'Arrive · Kassala Forward Store' shows 'PLANNED Jul 26, 2026 / ESTIMATED Jul 26, 2026 / ACTUAL —' with '0d vs plan' beneath it — an unreported arrival whose planned date has passed is rendered as on-plan. (2) The same '…d vs plan' label means planned-vs-actual on leg 1 and planned-vs-estimated on leg 3, with nothing distinguishing achieved lateness from forecast lateness. (3) The estimate carries no source badge and no revision history, even though the product has a SOURCE column in the Event log directly below and the before-frame tags every consignment 'Phone check-in' — and the modal's log contains exactly one event ('Jul 21, 2026 | De

**Proposed fix:** Give the delta an explicit basis and state: label it 'vs plan (actual)' when an actual exists and 'vs plan (forecast)' when it derives from the estimate; render a past-planned milestone with no actual as 'overdue · not reported' rather than '0d vs plan', with a distinct dot state. Alternative/additive: attach the reporting source and an 'as of' stamp to each estimate, and expose the estimate's revision history (from → to, on which event) beside the rail, using the card's currently empty right tw

### scene 3 · visual_polish · options

In the Milestones card the three date columns occupy roughly the left third (ACTUAL ends near x=460 in a card running to x=1140), leaving ~55–60% empty white; the scene's load-bearing micro-labels 'PLANNED / ESTIMATED / ACTUAL' are the smallest type in the frame, smaller than both the modal title and the secondary '9,000 cartons · 124.2 MT' line; '+9d vs plan' / '0d vs plan' hang as unheaded free text below their rows instead of sitting in a fourth aligned column; and the 'Consignment lines' table is clipped mid-row by the sticky 'Record an event / Close' footer. The subtitle 'Planned, estimated and actual — the three are never collapsed.' is on-card editorializing about the product's own de

**Proposed fix:** Lay the rail out as a four-column grid (Milestone | Planned | Estimated | Actual | Δ) spanning the card's full width with the delta right-aligned in its own headed column, raise the date/label type so the payload is the most prominent text in the card, and give the modal body its own scroll so the footer never clips a table row. Alternative: move the self-describing subtitle into an 'i' bubble on the card title so the definition is opt-in rather than asserted.

### scene 4 · design_soundness · options

Three interaction-model holes in one frame. (1) 'Record an event' is an on-page write control in the same footer as the claim of no write path, with nothing shown about what it may write, who it attributes to, or whether it can be undone. (2) The Event log's columns are WHEN / WHAT HAPPENED / WHERE / QUANTITY / SOURCE — no actor, no recorded-at, no immutable event id, and no event count, so 'Append-only' is unfalsifiable from the UI; 'Phone check-in' is a channel, not a person. (3) Nothing connects a derived value to its source: 'Depart · Khartoum Central Warehouse … ACTUAL Jul 21, 2026' and the log's 'Jul 21, 2026 | Departed' are the sole on-screen evidence of derivation and the reader must

**Proposed fix:** Add the two columns that make an append-only log auditable — an actor/'recorded by' and a 'received at' distinct from 'when it happened' — plus a visible event count ('3 events') so completeness is checkable; then make derivation clickable: each milestone ACTUAL and the status chip should link to (or reveal on hover) the specific event that set it, and 'Record an event' should visibly be an append-only form (no status field, immutable id + actor stamped on save).

### scene 5 · design_soundness · options

The provenance the concept_claim rests on is hover-only — the card says 'Hover a district for how its caseload was estimated' and nothing else — so on a projected, printed or screenshotted view the source note is unreachable, and it is never shown in the walkthrough. Separately the card offers no row-level action while the Exceptions card above it offers 'Reallocate to Askira Nutrition Centre' and 'Open SHP-2026-0202', so the row reading 'Borno IPC 5 | 48,232 | 16,399 | 34% | 31,833' is a dead end; and the ascending-coverage sort heads the table with 'Séno IPC 2 | 660' while gap is floored to '0' for Soum, hiding the 1,884-course surplus scenes 6–8 reallocate from.

**Proposed fix:** Pick among: (a) render the caseload source as a persistent inline element — a small source token in the district cell or a footnoted source column — instead of a hover-only tooltip; (b) add the same 'Reallocate to <node>' affordance the Exceptions card uses to each caseload row; (c) sort by GAP TO NEED (CHILDREN) descending, or make the column headers sortable, so the largest uncovered caseload heads the card; (d) show surplus as a signed value ('-1,884') instead of flooring to 0.

### scene 5 · visual_polish · options

Inconsistent control styling inside one column — 'Yobe IPC 4', 'North Darfur IPC 5', 'Borno IPC 5', 'Somali IPC 4', 'Soum IPC 5' are filled pills while 'Southern Darfur IPC 2', 'Gombe IPC 3', 'Kassala IPC 3' are unstyled plain text, so the IPC ladder cannot be read as a scale. The coverage column is traffic-light coloured with no threshold stated on the card or in its method note, and '145.8%' carries the same green as '91%' so a 46% over-supply into an IPC 5 district reads as 'good'. Precision and alignment are also inconsistent: '0%/34%/91%' beside '67.6%/145.8%'; '2' beside '2.5' in Weeks of cover; and every numeric column left-aligned so '4,116' and '48,232' do not line up by place value

**Proposed fix:** Pick among: (a) badge every IPC value with the same pill component, using the published IPC phase palette so the badge encodes the objective classification; (b) either state the coverage thresholds in the method note (and add a distinct, defined marker for >100%) or drop the fill and keep coverage as plain right-aligned tabular text; (c) right-align all numeric columns with tabular figures and one fixed decimal place per column.

### scene 6 · design_soundness · options

The expanded derivation — the artifact the whole scene is about — cannot be audited and contradicts the same page. It reads 'How this was ranked: 9 days late against 0.0 weeks of cover; the days after the store runs dry x the admission rate.' but (i) shows none of the operands it names (no cartons on hand, no admission rate, no run-dry date; 1,087/9 = 120.78/day appears nowhere), (ii) El Fasher is absent from the Weeks of cover table and is explicitly listed under '10 further nodes have received nothing yet and so have no run-dry date', so quoting '0.0 weeks of cover' and multiplying 'the days after the store runs dry' for it is undefined, and (iii) the queue reserves 2 of 8 slots for rows r

**Proposed fix:** Render the derivation as a labelled operand table rather than a sentence — on hand (cartons), weekly burn / admission rate, run-dry date, days uncovered, children = days x rate — so the number is checkable; give never-received nodes an explicit 'no stock ever received' derivation path instead of a spurious '0.0 weeks of cover'; and either move the zero-children 'No children go without' rows out of the ranked queue into a separate 'absorbed delays' section, or drop their 'Expedite' affordance and

### scene 6 · visual_polish · options

Several product-side visual defects in this one frame: the white 'Food insecurity (IPC phase)' legend card overlaps and clips the 'mapbox' attribution wordmark at the map's bottom-left; the El Fasher card prints '1,087 children lose a full course at El Fasher Distribution Hub' and then '1,087 children lose a full course' as consecutive lines; the load-bearing 'How this was ranked:' derivation is the smallest, lowest-contrast grey italic on the card and uses a lowercase letter 'x' as a multiplication sign; the WEEKS OF COVER column mixes bare integers with one-decimal values ('2', '3', '4', '7' beside '0.6', '2.5', '25.2') and COVERAGE mixes '0%' with '67.6%' and '145.8%'; four node dot colou

**Proposed fix:** Move the IPC legend clear of the attribution (or dock it top-left under the layer toggle); drop the duplicated card headline; promote the derivation to normal body weight/contrast with an operator glyph; fix the number formatters to one decimal everywhere for weeks of cover and coverage; add a dot-colour key to the map legend; and print the numeric threshold behind each colour band (or move the colour into an on-demand 'i' bubble beside the metric definition) so the emphasis encodes a stated fac

### scene 7 · design_soundness · options

The queue contradicts its own stated ranking rule, and the origin taxonomy is ambiguous. (a) Page text: 'Exceptions / Ranked by the children behind each one, not by tonnage.' The rendered order is 1,087 → 240 → 87 → 60 → 47 → '907 cartons at Djibo Distribution Hub expire before they can be used / 907 children lose a full course' LAST — 907 children sorted below 47. (b) Origin is split across a pink 'Partner shortfall' pill occupying the same slot and colour family as the derived 'Short receipt' pill, plus a separate blue 'Raised by Komadugu Health Initiative' pill, with no key distinguishing exception TYPE from ORIGIN. (c) The partner row is the only row lacking the 'How this was ranked:' ex

**Proposed fix:** Either (i) make expiry-risk rows rank by their children figure like every other row so the stated rule holds, or (ii) restate the rule to name the actual sort key (e.g. 'ranked by children at risk within the response window; expiry risk listed after'). Separately, collapse origin into one defined slot — keep the type pill for the exception class and render origin as a single consistently-styled provenance chip (org · date · 'reported' vs 'derived by OES') — and give partner rows the same 'How th

### scene 7 · visual_polish · options

Multiple product-side visual defects in this frame. Map panel: the IPC legend card's lower edge sits over and clips the 'mapbox' attribution wordmark; the canvas leaves ~65px of dead white inside the card below it; place labels are truncated at the panel edges ('Mogad', 'Aden', Lagos/Benin cut); and four distinct point-marker colours plus corridor flow lines have no legend entry at all — the legend decodes only 'Food insecurity (IPC phase) 1—Minimal … 5—Catastrophe / Famine'. Exception card: the same date appears twice in two formats on adjacent lines ('87 children at Askira Nutrition Centre by 6 August' then '87 children lose a full course by Aug 6, 2026'), and on this card the second line 

**Proposed fix:** Move the legend so it cannot overlay the Mapbox attribution and size the canvas to its container; add point-marker and corridor entries to the map legend; normalise dates to one format per card and make the partner card's sub-line carry information the headline doesn't (e.g. the reported admission rate); drop the amber fill on GAP or define the threshold it encodes in the column's info bubble; and widen the exception column against the under-used sidebar.

### scene 8 · design_soundness · options

Four interaction/logic incoherences on the payoff row. (1) It is marked Closed while still displaying open-state styling and a present-tense loss assertion. (2) It claims to have created 'a real consignment with planned milestones' but exposes no shipment ID and no link, while every sibling card offers 'Open SHP-2026-0402' / 'Open SHP-2026-0202' — a dead end from the record to the thing it created. (3) 'planned to arrive 6 August' is closed against a node the product's own table gives 'RUNS DRY Aug 4, 2026', and nothing warns that the answer lands after the shelf empties. (4) 'Gombe … is a day's run away' sits beside a six-day planned arrival from a Jul 31 decision.

**Proposed fix:** On a resolved row: link the created shipment by ID next to the 'Closed' pill (mirroring the existing 'Open SHP-…' affordance), and render an explicit ETA-vs-required-by delta so an arrival after the destination's run-dry date is shown as 'answered, arrives 2 days late' rather than silently as Closed. Then either reconcile the planned arrival with the stated transit time or show the schedule component that explains the six days.

### scene 8 · visual_polish · options

The white IPC legend panel's lower-left corner overlaps and clips the 'mapbox' attribution wordmark in both frames. On the payoff card, '87 children at Askira Nutrition Centre by 6 August' and 'Closed 87 cartons from Gombe Distribution Hub…' are set at near-identical size and weight, so the demand figure and the supply figure are visually interchangeable. The three chip colours ('Partner shortfall' pink, 'Raised by Komadugu Health Initiative' blue, 'Closed' green) have no key. In the pipeline table only one of four numeric columns carries its unit ('REQUIREMENT 45,000 cartons' vs bare 'SHIPPED 15,000', 'DELIVERED 15,000', 'GAP 30,000'), and the GAP pills are amber-filled against no stated th

**Proposed fix:** Reposition the legend (or add bottom padding to the map container) so the Mapbox attribution is never overlapped. Differentiate the resolution line from the card title typographically and label the two 87s by unit inline ('87 children' / '87 cartons'). Carry the unit into the SHIPPED/DELIVERED/GAP headers, and either state the threshold the amber GAP pill encodes or render the gap as a plain figure.


## oes-money-to-child

### scene 1 · design_soundness · mechanical

The 'Stage by stage, per contract' card subtitles itself 'Obligated, disbursed and delivered are tracked separately and never merged' and ships two figures, not three: there are OBLIGATED and DISBURSED value columns and no DELIVERED column. Delivered exists only as an ~8px unlabelled bar with no value, no scale and no tooltip. The scene's concept_claim ('three separate figures per contract') is falsified by the card that asserts it.

**Proposed fix:** Add a DELIVERED value column beside OBLIGATED and DISBURSED, in whatever unit delivery is recorded (MT and/or courses).

### scene 1 · visual_polish · mechanical

The legend reads 'obligated · delivered · disbursed' while the column header immediately below reads 'OBLIGATED -> DISBURSED -> DELIVERED'. Two contradictory orderings of the same three-stage sequence, rendered as a pale tint plus two greens close enough in hue that a viewer cannot reliably tell which bar is which — on the one card whose whole point is that the three stages never merge.

**Proposed fix:** Order the legend swatches to match the header's stage chain and separate the two greens into a discriminable pair (or drop the legend and label the bar values directly).

### scene 1 · claim_reality_coherence · mechanical

The displayed DISBURSED column sums to $547k ($0 + $0 + $502k + $45k) against a $546k Disbursed headline. Rounding explains it; a reader with a calculator on a page that promises reconciliation does not care.

**Proposed fix:** Compute the KPI from the same rows as the table (no independent rounding), or show one more significant figure so the column sum lands on the headline.

### scene 1 · design_soundness · mechanical

No as-of date, reporting period or scope stamp exists anywhere on either surface, while rows carry dates of Jul 26 / Jul 30 / Jul 31 / ETA Aug 2. An auditor cannot cite a disbursement or a coverage figure without one, and its absence is what lets a future-dated 'Delivered' row pass unnoticed (see F23).

**Proposed fix:** Stamp both surfaces with 'as of <date>' and the period covered.

### scene 1 · design_soundness · mechanical

The three words the whole concept rests on — obligated, disbursed, delivered — carry no definition affordance on the card that presents them, although 'i' bubbles exist for lesser figures elsewhere on the same page. The IATI activity identifiers (US-GOV-1-OES-C-2026-*) are the page's one external-verification affordance and are dead grey monospace text that wraps mid-token in 4 of 4 rows.

**Proposed fix:** Add an 'i' bubble defining the three stages on the card that presents them, and make each IATI activity identifier a link to the published IATI record.

### scene 2 · visual_polish · mechanical

Three node labels are ellipsised — 'FY2026 Emergency Food S…', 'FY2026 Famine Preventio…', 'Rift Valley Therapeutic…' — and the full names appear NOWHERE else on the page. The two truncated ones are the appropriation names, i.e. the exact entities the scene's claim is about ('each contract draws on its own envelope').

**Proposed fix:** Wrap node labels to two lines or place them outside the node rectangle so no source name truncates.

### scene 2 · visual_polish · mechanical

The smallest flow renders in a completely different style from every other node: 'Blue Nile Freight Co · $128k' and 'Sudan · $128k' are crammed onto one line at the chart's bottom edge against hairline strips, with the freight label sitting over the partner-to-country ribbon channel so it reads as a label ON the ribbon. It looks like an unhandled too-small-node fallback.

**Proposed fix:** Give the small-node case a real layout: enforce a minimum node height and leader-line the label out to a reserved gutter instead of overlaying the ribbon.

### scene 2 · concept_clarity · mechanical

The appropriation nodes are labelled with their obligated slice only ('FY2026 Emergency Food S… $2.9M'), which any funder reads as the envelope's size — the appropriation is $70.5M. The judge's 5-second read of a funding chart got the most important number wrong by an order of magnitude, and the correction is small grey caption text below.

**Proposed fix:** Label each appropriation node with both figures — obligated slice of envelope total (e.g. 'FY2026 Emergency Food Security · $2.9M obligated of $41.2M') — so the node cannot be misread as the envelope.

### scene 3 · claim_reality_coherence · mechanical

The card says 'Stated as a chain, so every step can be checked'. Doing exactly that gives 502,000 / 12,000 = USD 41.83; the screen prints USD 41.80. A 3-significant-figure operand ('$502k') cannot support cent precision at all — anything in [$501,500, $502,499] rounds to $502k, spanning USD 41.79-41.87.

**Proposed fix:** Print the unrounded disbursement on the first rung so the division closes exactly, and render the result only to the precision the shown operands support.

### scene 3 · design_soundness · mechanical

The '166 MT -> 12,000 cartons' rung requires 13.8 kg per carton (150 x 92 g). Neither kg-per-sachet nor kg-per-carton appears in the viewport or anywhere in the page text, so the middle link of a chain advertised as checkable cannot be checked. The 1-carton-=-1-course identity that turns cartons into children is likewise never printed — it exists only in the narration, which is why two adjacent tiles show an identical 12,000 and read as a copy-paste bug rather than a deliberate 1:1.

**Proposed fix:** Label the arrows with the constants: '/ 13.8 kg per carton (150 x 92 g sachets)' between rungs 2 and 3, and '1 carton = 1 full course' between rungs 3 and 4.

### scene 3 · design_soundness · mechanical

The ladder opens at '$502k disbursed on supply contracts, against confirmed delivery' while the page headline three inches above reads '$546k Disbursed'. Two near-identically-labelled disbursement figures differing by 8%, with no reconciliation line. The difference is the $45k Blue Nile Freight disbursement — excluding delivery cost from a cost-per-outcome figure is a substantive, contestable methodological choice presented as none. Separately, the narration's specific claim that 'consignments in transit are excluded, and it says so' is false of this screen: neither 'in transit' nor 'excluded' appears in the viewport or in the full page text.

**Proposed fix:** Add one reconciliation line under the ladder naming the actual exclusions, e.g. 'From $546k disbursed: excludes $45k freight (OES-C-2026-SD1); consignments in transit are excluded.'

### scene 3 · concept_clarity · mechanical

'cost per child treated' is contradicted by its own operand — the terminal rung reads 'children given a full course, paid for and confirmed', which is a procurement fact, not a treatment outcome — and by the same page's own 'Children treated — the figure, and the measurement' card two cards below, which exists specifically to distinguish those two things.

**Proposed fix:** Rename to 'cost per full course paid for and confirmed' and cross-reference the measured-recovery card from its 'i' bubble.

### scene 4 · visual_polish · mechanical

The 28-row consignment table has no sticky header, so any scrolled user (not just this capture) faces eight unlabelled columns — '900 cartons', '900', 'Jul 14, 2026', 'Delivered' — with nothing saying what any column is. Compounding: COURSES ON ARRIVAL restates QUANTITY verbatim on 27 of 28 rows and is an unexplained '—' on the 28th; PARTNER and COMMODITY repeat one identical string down 18 rows, eating ~35% of the width; and the Confirmed vs Delivered pills — the distinction that decides which rows count — are near-identical pale green.

**Proposed fix:** Make the table header sticky, drop COURSES ON ARRIVAL in favour of one 'one carton = one full course' note in the QUANTITY header, group or subdue repeated PARTNER/COMMODITY values, and give each status its own visually distinct pill.

> **REOPENED, 31 July 2026.** The sticky header shipped in #1084 never engaged:
> every `.data-table` sits inside `.table-wrap`, whose `overflow-x: auto` makes
> the wrap the sticky element's scrollport, so the topbar-height offset parked
> the header row 54px INTO every table — over the first data rows, eating their
> clicks (#1093, #1096 removed the offset). Headers are back at their natural
> position and first rows are clickable, but a scrolled long table once again
> shows unlabelled columns. The real fix is a layout decision — the wrap owning
> vertical scrolling (max-height + overflow-y) or an unwrapped table — and is
> left for a human eye alongside the other layout calls above.

### scene 4 · claim_reality_coherence · mechanical

'Savanna Nutrients Ltd | RUTF | Maiduguri Distribution Hub | 15,000 cartons | Jul 31, 2026 | Delivered' is future-dated relative to today (Jul 30, 2026) and that single row carries 92% of Borno's entire reported coverage (15,000 of 16,399). An auditor treats a future-dated completion as a control failure, not a rounding issue.

**Proposed fix:** Re-date the Maiduguri consignment to on-or-before the as-of date, or set its status to In transit; and add the as-of stamp from F04 so any future-dated row is self-evidently wrong.

### scene 4 · visual_polish · mechanical

Two of the four government KPI tiles carry the identical integer under near-identical labels: '25,863 Cartons delivered into districts' and '25,863 Courses delivered'. Honest (a carton is a course), but it spends half the headline row restating one number on a surface whose page lacks the figure a ministry actually wants — national coverage of need.

**Proposed fix:** Merge the two tiles into one ('25,863 cartons = 25,863 full courses') and use the freed slot for Nigeria's overall coverage of need.

### scene 5 · visual_polish · mechanical

In 'Stock by location', Damaturu Distribution Hub prints 0 while drawing a pale bar to ~50% of the track — i.e. the 10,000-carton in-transit consignment on a 20,000 scale. That quantity is printed nowhere, there is no legend distinguishing the pale fill from the solid fill, and the bars carry no unit and no scale. A reader sees a half-full bar labelled zero.

**Proposed fix:** Render no fill for a zero on-hand value; show in-transit as an explicitly legended second series with its own printed value; label the bars' unit and scale.

### scene 5 · visual_polish · mechanical

Coverage pills are colour-coded against a threshold stated nowhere: on the government view 0% and 34% render red and 91% green; on the funder view 23.4% and 33.3% red, 67.6% amber, 106.1% green. Per the OBJECTIVE-DATA STANDING RULE, colour is earned only by a value crossing a STATED threshold — this is an unexplained verdict stamped on the measure, on the one product whose entire thesis is that interpretation must be stated so it can be challenged. It currently colours warehouse stock green (see F27). Compounding, IPC 3 renders as bare grey text while IPC 4 and IPC 5 render as filled pills, within three rows.

**Proposed fix:** State the banding rule beside the COVERAGE column (or in its 'i'), render >100% as a neutral over-delivery marker rather than green, and give IPC 3 the same pill treatment as IPC 4 and IPC 5.

### scene 5 · design_soundness · mechanical

The card's footnote instructs 'Hover a district name for how its caseload was estimated', and that disclosure is a native HTML title attribute: OS-rendered, unstyleable, ~1s delayed, keyboard-inaccessible, absent on touch, and invisible in the capture (the frame shows the cursor on Borno and no tooltip). It is also anchored to the district NAME, two columns from the CHILDREN NEEDING TREATMENT figure it explains, with no dotted underline or 'i' glyph marking it interactive. The caseloads themselves (48,232; 10,400) carry no prevalence rate, population, survey name, reference period or uncertainty bound on the face.

**Proposed fix:** Replace the title attribute with the product's own 'i' popover, anchored to the CHILDREN NEEDING TREATMENT cells, containing the prevalence rate, under-5 population, survey name, reference period and uncertainty bound.

### scene 5 · concept_clarity · mechanical

The sentence that justifies the entire concept is logically invalid, and it is invalid in three places at once. On screen: 'Tonnage cannot distinguish a large delivery into a large caseload from a small one into a small caseload' — but coverage cannot distinguish those two either (both are the same ratio), and a large delivery and a small one plainly DO differ in tonnes. The narration repeats the same broken construction ('A large delivery into a large caseload and a small delivery into a small one look identical in tonnes'), and why_brief S4's rationale carries a third variant of the same error. The real contrast is two deliveries of EQUAL tonnage into UNEQUAL caseloads.

**Proposed fix:** Restate on screen, in the narration, and in why_brief S4: 'Two deliveries of the same size into different caseloads look identical in tonnes. Coverage tells them apart.'

### scene 5 · design_soundness · mechanical

The card has no in-transit or committed column, so Yobe reads as abandoned (0 delivered, 0%, 18,960 uncovered) while the same page shows 10,000 cartons In transit to Damaturu with ETA Aug 2 — 53% of Yobe's need landing in 72 hours. For the stated use ('where do I put my own state resources'), omitting the pipeline is the single most decision-distorting gap on the card. There is also no national total row, though it is computable from the three rows shown (25,863 / 77,592 = 33%; 51,729 uncovered).

**Proposed fix:** Add an in-transit / committed column and a national total row to the coverage card, and default the sort to children-still-uncovered descending.

### scene 5 · visual_polish · mechanical

The column header reads DISTRICT and the values are Nigerian STATES with an IPC token glued into the name string ('Yobe IPC 4', 'Borno IPC 5', 'Gombe IPC 3'), while the actual districts/LGAs (Ngala, Monguno, Gwoza, Mafa, Konduga) appear only as consignment destinations. To the stated audience — a federal ministry official — Borno labelled as a district is simply wrong, on the exact term the scene's title rests on; and that state/LGA ambiguity is what makes F22's exclusion invisible.

**Proposed fix:** Rename the column to STATE (or aggregate to genuine LGA rows) and move the IPC phase into its own column with the phase name available on demand.

### scene 6 · design_soundness · mechanical

The DISCHARGE OUTCOME table's SHARE column sums to 169.6%. Its four true discharge rows are exact against n=79 and sum to 100.0% (Recovered 64 / 81.0%, Defaulted 10 / 12.7%, Transferred 3 / 3.8%, Non-response 2 / 2.5%). A fifth row — 'Still in treatment 55 69.6%' — applies the DISCHARGED denominator (55/79) to children who were never discharged, under a discharge-outcome header. Against the 134-child observed cohort the figure would be 41.0%. On the one screen whose entire pitch is methodological honesty, this is the most damaging possible defect.

**Proposed fix:** Lift 'Still in treatment' out of the DISCHARGE OUTCOME table into a stated line beside it ('134 children observed; 79 discharged, 55 still in treatment') so the four discharge rows keep their n=79 denominator and the share column sums to 100.0%.

### scene 6 · design_soundness · mechanical

The measured figure's observation base is never stated. 58,251 courses sit beside a 134-child observed cohort (0.23%), with no sampling frame, no site or country scope for the 79 discharged children, and no uncertainty — while the only site shown is Damboa, in one of four delivery countries. The batch modal states its own base scrupulously ('that sample is 6 of the 314 children this batch fed — a rate from 6 children carries a wide interval'); the headline card the whole scene is named for extends none of that discipline to itself. Rigor is applied where it is safe and withheld where it would sting.

**Proposed fix:** Under the '64 / 79 Recovered' tile, add the base in the modal's own plain register: '134 of 58,251 courses delivered (0.2%), observed at N sites', plus how the observed children were selected.

### scene 6 · design_soundness · mechanical

The scene claims both figures are 'reported side by side with their methods', and one method is a five-word stub: the 58,251 tile's 'i' says only 'Arithmetic on the supply record.' Neither the carton count nor the treatment divisor the narration names ('cartons delivered, divided by a treatment factor') appears anywhere.

**Proposed fix:** Put the actual arithmetic in that 'i' bubble (cartons x sachets / sachets per course = courses) and state on the card how 58,251 relates to the 33,000 under contract.

### scene 6 · claim_reality_coherence · mechanical

The discharge-outcome table has no mortality/death row, and the footnote cites only Sphere's recovery (>75%) and defaulting (<15%) thresholds — omitting the death-rate criterion — on a screen that explicitly invokes Sphere as the standard the sector grades itself against. A paediatric nutrition clinician notices within seconds that the one omitted outcome category is the one whose absence can only raise the recovery share.

**Proposed fix:** Add a 'Died' row to the discharge-outcome table (zero is a legitimate, informative value on synthetic data) and add Sphere's death-rate threshold to the footnote alongside the other two.

### scene 6 · visual_polish · mechanical

'2 visits over 1 weeks' — an unpluralised string, in 12px type, on the single highlighted row, i.e. the most-looked-at text in the modal. Separately, the focused row's ID wraps to three lines ('DAM-' / '2607-' / '015') while five identical-length IDs beneath sit on one line each — a layout regression triggered by selection, in the frame's focal row.

**Proposed fix:** Pluralise the visit-window string, and widen the name column so IDs never wrap when a row is selected.

### scene 6 · visual_polish · mechanical

On-screen editorialising on an evidence artifact: the drill link reads 'The gap is the finding, and it is followable: follow batch LOT2602B to the children it treated' — an interpretive conclusion stamped on the data, telling the reader what to think. (Contrast the 81% tile's 'Sphere expects above 75%' inside an 'i', which is exactly the right on-demand pattern.)

**Proposed fix:** Reduce the link to what it does — 'Follow batch LOT2602B to the children it treated' — and let the two unreconciled figures be the finding.

### scene 2 · design_soundness · options

The card's own subtitle says 'Appropriation -> partner -> country -> commodity delivered. Totals reconcile at every stage' and only THREE node columns are drawn — no commodity stage exists. Worse, an unlabelled node-shaped pale rectangle sits between the partner and country columns exactly where a fourth stage would go, so a cold viewer reads it as an unlabelled column.

**Proposed fix:** Either draw the commodity column, or change the subtitle to the three stages actually rendered. Do not ship the mismatch; a reader cannot verify 'reconciles at every stage' for a stage that is not there.

### scene 2 · design_soundness · options

No ribbon carries a value and no ribbon is tinted by source, so the one relationship the diagram exists to express — this contract drew on THAT envelope — can only be back-solved by arithmetic coincidence ($1.9M + $872k + $128k = $2.9M). And the verification the caption offers, 'Every column sums to $4.9M obligated', is preserved identically under the first-envelope mis-attribution the concept names as the failure mode: the offered check does not test the claim.

**Proposed fix:** Either tint each ribbon by its source appropriation and label every link with its value, or state (and expose) the check that actually tests the claim — per-appropriation outflow reconciliation, each envelope's outbound links summing to its obligated total.

### scene 3 · design_soundness · options

THREE different 'courses delivered' magnitudes sit on one funder page under near-identical labels, with no stated relationship: '33,000 Courses delivered under contract' (KPI, = 455 MT), '58,251 Courses delivered' (the two-figures card, and exactly the coverage table's sum of 6,388+25,863+20,000+6,000), and '12,000 children given a full course' (the ladder, = 166 MT). 58,251 EXCEEDS what the same page says is under contract. Ethiopia alone reads 20,000 courses delivered in the coverage table and 12,000 in the ladder. Two tonnages likewise: 455 MT vs 166 MT. This is precisely the collapse the whole narrative indicts, committed on its own page.

**Proposed fix:** Either namespace all three ('12,000 paid for and confirmed' / '33,000 under contract' / '58,251 delivered to date') and add one line stating how they relate, or reduce the page to a single stated basis. Which basis is authoritative is a product decision — hence options.

### scene 3 · motion_friction · options

The scene's title is 'Cost per child, with its method on screen', and the method is not on screen: the only affordance is an unopened ~10px low-contrast 'i' beside the figure, and the trace (scroll_to, hold, scroll_to, hold) never opens it. Load-bearing methodology behind a hover is also invisible on touch and to keyboard/screen-reader users, so this is not only a capture gap.

**Proposed fix:** Either surface the method inline on the ladder card (see F15/F16) rather than behind an 'i', or promote it to a labelled 'How this is calculated' affordance and open it on camera during the scene.

### scene 4 · design_soundness · options

The scene's claim is server-side country scoping and the product ships no positive artifact of it: no country column, no scope chip, no scoped row count, no statement of the filter applied, nothing in the chrome beside the persona badge. The word 'Nigeria' does not appear anywhere in the captured viewport. The stated audience need — 'satisfy myself about what this dashboard can and cannot see' — has no affordance at all, and the claim is an ABSENCE that no screenshot can evidence unless the product asserts the scope positively.

**Proposed fix:** Either add a persistent scope statement that survives scrolling (a top-bar chip 'Scope: Nigeria — applied server-side' plus 'N consignments, all terminating in or moving within Nigeria' above the table) plus a pinned COUNTRY column, or add a dedicated 'what this view can see' disclosure. The redundancy IS the proof.

### scene 4 · design_soundness · options

The consignment rows do not reconcile with the page's own totals. Summing every row marked 'Delivered' gives 33,246 cartons against a '25,863 Cartons delivered into districts' headline and a district table totalling 25,863. The 7,383-carton difference is TEN visible rows marked Delivered to Borno nutrition centres (Ngala 1,627, Dikwa 1,635, Gwoza 1,126, Monguno 900, Damboa 657, Konduga 469, Kukawa 375, Mafa 282, Magumeri 274, Askira 38), excluded with no per-row indicator — only an eight-word footnote two cards away ('counted once, where each crossed a district boundary'). Kano Central Warehouse's 20,000 is counted in 'what has landed' while flagged Confirmed, and appears in no coverage row 

**Proposed fix:** Either add a per-row 'counted in district totals' marker plus an explicit reconciliation chain above the table ('33,246 in recorded movements -> 7,383 onward movements within a district, not re-counted -> 25,863 counted once at district entry'), or make each coverage cell expand to the exact rows summed. Pick one — both are defensible, so this needs an author decision.

### scene 4 · design_soundness · options

Ten row-pairs are byte-identical in partner, commodity, destination, quantity and courses, differing only in a date and a pale status pill (274/274, 282/282, 375/375, 469/469, 1,126/1,126, 1,635/1,635, 657/657, 38/38, 900/900, 1,627/1,627). Nothing on screen says Confirmed and Delivered are two lifecycle stages of ONE consignment rather than two shipments, so the cold read — and the read of anyone summing the QUANTITY column — is double-counting.

**Proposed fix:** Either collapse each consignment to one row with a status timeline (Confirmed Apr 22 -> Delivered Jul 18), or give the two stages distinguishing identifiers and say which one totals consume.

### scene 5 · design_soundness · options

The coverage numerator is warehouse stock. Borno's 16,399 = Maiduguri Distribution Hub 15,000 + Bama Health Post 1,399; Gombe's 9,464 = the Gombe Distribution Hub consignment. The 'Stock by location — What has landed at each storage point' card in the SAME viewport prints Maiduguri 15,000 and Gombe 9,464 as on-hand stock. So 24,464 of the 25,863 counted courses (94.6%) have not left a hub, while the columns read COVERAGE OF NEED and CHILDREN STILL UNCOVERED and the card is titled 'Coverage by district, not tonnage delivered'. Gombe's '91% covered, 936 still uncovered' is composed entirely of undistributed stock.

**Proposed fix:** Either compute coverage from consignments delivered to service points only (nutrition centres / health posts), or keep the current basis and rename the columns honestly ('courses in district', 'supply vs need') and drop CHILDREN STILL UNCOVERED — you cannot derive treated children from warehouse stock. This is a definitional choice only the author can make.

### scene 6 · visual_polish · options

Residuals on the already-fixed MUAC charts (shared row height and drawn+labelled 115/125 rules are CONFIRMED present and credited, not re-raised): the threshold labels are ~8px grey type set on the dashed rules inside pale green and amber fills — a likely WCAG AA failure — and they appear on the focused row ONLY; rows 2-6 draw the rules unlabelled. 60-70% of each chart is band no datum ever enters, so all six series hug the bottom of the red band and a +8 mm climb is visually indistinguishable from +3 mm. Each series is two points drawn 560px wide with no x-axis and no dates, and only the focused row discloses its time base. The sixth of six sampled children is clipped by the sticky modal fo

**Proposed fix:** Either clip each chart's y-range to the data plus one band of headroom (making the climb visible) or keep the full WHO range and add a magnified delta readout; and in both cases label the 115/125 rules on every row at body-text size and contrast, add a dated x-axis (or draw two visits as a labelled slope pair rather than a stretched trend line), show visit count and mm delta on every row, and size the modal so all six sampled children fit.

### scene 6 · design_soundness · options

The trace dead-ends on a product whose claim is followability: SHP-2026-0902, Damboa Nutrition Centre and all six child IDs are inert text, the modal's strongest control by far is a filled green 'Close' (alongside a bare 14px '×' — two dismiss affordances of wildly different weight in one dialog), and there is not one control that continues the chain. The narration's 'follow a batch through the distributions it fed' is fulfilled by a sentence rather than by navigation. 'In Treatment' also prints six times identically in the column position where the discharge outcome would go.

**Proposed fix:** Either make the shipment, the site and each child ID links to their records and demote Close to a secondary control, or add one genuine forward action (the distribution record the batch fed) so followability is exercised rather than asserted.


## oes-supply-base

### scene 1 · dashboard-not-actionable · mechanical

The interaction model holds at a high level — the nav names the four real objects and the queue points at a named round — but affordances are hand-wavy in three places: the review queue has no per-row action so it cannot be worked from the queue, the four key figures give no signal whether they drill through to filtered views, and the amber tile state implies a filter that does not exist.

**Proposed fix:** Add a per-row 'Review' action to 'Applications awaiting review', make each of the four key figures link into its filtered view (applications queue, registry filtered to live qualifications, lots awaiting award, open rounds), and give the Solicitations panel a status filter defaulting to open/in-flight so the actionable record is the first row.

### scene 2 · act-claimed-not-performed · mechanical

The narration says 'Ada opens a round and names what they are collecting interest in' but the action_trace performs no such act — one nav click on '.navitem:has-text(EOI rounds)', a wait_for and a hold. The 'New round' button is never touched and the round is already status 'Open' with all four categories pre-declared, so the frame is a pre-existing record, not the result of Ada's narrated act. Deduction rule: narration describes a core interaction the action_trace never performs (max 2). Compounding: the closed round's action cell is a bare '—' dead end for 14 applications, 'Close' is an unguarded lifecycle transition styled softer than the benign 'Review', and APPLICATIONS=8 sits unreconci

**Proposed fix:** Perform the act on camera — click 'New round', fill the name, select the four categories, submit, then transition draft to open, so the round in the table is the one the viewer watched Ada declare; and in the product give the closed round a real action ('View outcomes') instead of '—', put a confirmation behind 'Close' stating what closing prevents, and label the Review queue 'showing 4 of 8 · awaiting review' as a removable filter so the two counts reconcile.

### scene 2 · flat-card-hierarchy · mechanical

Alignment is tight and there is a real type scale, but the two cards carry identical visual weight so the object/derived relationship is flat, the left rail's panel terminates mid-page leaving a hard edge and a bare column, row actions mix a filled primary ('Review') with an outline secondary ('Close') and an empty '—' cell in the same column position, and the Rounds table is visually thin because its defining attributes (dates, id, owner) are absent.

**Proposed fix:** Extend the left rail's surface to full viewport height so it reads as a rail rather than a truncated card; give the Rounds card the heavier visual treatment as the page's subject, with the open round's window rendered as a dated range plus a days-remaining marker; and replace the empty '—' action cell with a real secondary action so the column has a consistent control in every row.

### scene 2 · act-claimed-not-performed · mechanical

The narration describes a multi-step declaring operation while the trace contains only a tab navigation, a wait and a hold, so a viewer cannot tell from the capture what was done — and the artifact the narration is about (the Rounds table with its four category chips) sits buried in the lower third beneath a larger Review queue the narration never mentions.

**Proposed fix:** Script the real act — click 'New round', fill the round name, select the four categories, submit, transition to open — with a hold on the resulting Rounds row so the categories land under the words that name them; and scroll_to the Rounds card so it is the framed subject before the voiceover starts.

### scene 3 · act-claimed-not-performed · mechanical

The interaction the narration describes as the core of the scene — declaring commitments and submitting, which is what CREATES the frozen snapshot — is absent from the action_trace entirely (no fill/select/type/press, no Submit click; the row click opens an application already stamped 'Submitted Jul 20, 2026'). The product's freeze also has no representation in the UI: no snapshot id, no freeze timestamp distinct from the submission date, no immutability record. Deduction rule: narration describes a core interaction the action_trace never performs (max 2).

**Proposed fix:** Perform the application on camera — open Amina's EOI form, fill capacity / regions / lead time for at least one category, click Submit, and land on the resulting panel — and give the freeze an object in the product (a 'Profile snapshot recorded <timestamp> · #<id> · immutable' line on the submission) so the mechanism the flow depends on is visible and pointable.

### scene 3 · diff-columns-off-grid · mechanical

Three product-side visual faults land inside the one panel the scene exists to show: the paragraph columns and the table columns sit on different grids ('Frozen at submission' at x~155 and 'Live profile today' at x~721, while the table puts AS SUBMITTED at x~335 and LIVE TODAY at x~645 — the live column shifts ~76px left, breaking the left=frozen/right=live spatial contract), the two diff columns are unequal widths, and the modal opens on a sliced-off orphan card (~10px sliver above 'Commitments'). The footnote also editorializes the conclusion rather than offering it on demand.

**Proposed fix:** Put the whole comparison on one grid: the 'Frozen at submission' / 'Live profile today' headings and the AS SUBMITTED / LIVE TODAY table columns must share identical x-positions and equal widths with a visible column divider; scroll the modal to a card boundary (scroll-margin) so no partially-clipped card is ever the top of frame; and move 'editing the profile cannot reach it' out of the asserted footnote into an openable info affordance on the panel title.

### scene 3 · act-claimed-not-performed · mechanical

The narration describes a multi-step operation (declare capacity, regions, per-category lead times, submit) while the trace performs only camera moves and two navigation clicks, so a viewer cannot tell what was done; the scene also opens from a frame showing a different persona's procurement queue, so the clip starts on Ada's screen while the voiceover says 'Amina applies'.

**Proposed fix:** Rescript scene 3 as an act: land on Amina's application form after the persona switch has settled, fill the three declared fields with a visible hold on each, click Submit, hold on the confirmation, and only then scroll_to 'What the reviewer is assessing' — and trim the clip so the scene never opens on the previous persona's page.

### scene 4 · canonical-frame-is-the-aftermath · mechanical

The fits-but-mis-framed case at its most extreme: the entire narration is about the decision modal — the per-category rows and 'the date the pass expires beside the one that carries it', per the trace's own hold notes — and that modal demonstrably fits one viewport (the before-frame shows it), yet the scored frame is taken after it closed, so the narrated artifact is 100% out of frame. NOTE: this is the side-effect of correctly adding the 'Record decision' click — the act now persists (and scene 5's registry reads the granted Jan 21 2028 expiry), but the canonical still moved to the aftermath.

**Proposed fix:** Re-script so the canonical snapshot is the decision state, not the aftermath: take the scene's still on the hold AFTER Qualify(RUTF) and Reject(Therapeutic milk) are set and the expiry is on screen — modal fully in frame, both verdicts visible — and keep the 'Record decision' + toast beat as a following hold or second still rather than the frame that represents the scene.

### scene 5 · registry-row-dead-end · **FIXED** — partly false — rows were always clickable; granted_by + source round added

A first-time user cannot reproduce or defend the eligibility judgment from this interaction model: rows render a hover highlight under the cursor but are not navigable, so the promised route to the qualification record (granted date, granting reviewer, certificate, source EOI application) is a dead end, and there is no affordance at all — no expired view, no as-of control — for the expiry mechanism the screen exists to demonstrate.

### scene 5 · unlabelled-native-selects · mechanical

Alignment, spacing, contrast and date formatting are clean and the table is fully legible, but the load-bearing number is set at ordinary card-title weight with no hero treatment, the three filters are unlabeled native OS selects inconsistent with the app's own styled control language, and the bottom ~55% of the viewport is empty.

**Proposed fix:** Promote the filtered result to a real hero — a large numeral with a plain-language qualifier ('3 suppliers qualified for RUTF in Nigeria') — add visible field labels above each of the three filters ('Category', 'Country', 'Expiry'), and restyle the selects to the app's own control language.

### scene 5 · expiry-filter-never-exercised · mechanical

The table fits and is fully, settledly in frame with no spinner, blank open or dead end, and both narrated filter actions were performed as real effecting selects (both ok:true) — but roughly a third of the voiceover is about expiry and lapse while the camera never exercises the third ('Any expiry') filter or frames any expiry-related element, and the synthetic cursor rests on top of 'Jan 21, 2028', clipping an expiry date in the scene about expiry.

**Proposed fix:** Add a third select on the expiry filter to 'Expiring in 90 days' with a hold timed to the lapse sentence, and move the cursor clear of the table before the final hold so no expiry date is occluded.

### scene 6 · act-claimed-not-performed · mechanical

The narration describes two core interactions — publishing the solicitation, and the server deciding who receives it — and the action_trace performs neither (one nav click, a wait_for, a scroll_to, a hold; the row was already 'Published'). Deduction rule: narration describes a core interaction the action_trace never performs (max 2). Separately the row carries two undefined, competing completion affordances ('0 / 4' beside STATUS: Published).

**Proposed fix:** Add the step that exercises the gate — after landing on Ada's list, load the supplier-side solicitation list for a supplier holding no live matching qualification and hold on the solicitation being ABSENT from it — and in the product collapse '0 / 4' and STATUS into one dated lifecycle field, or label the ratio ('lots awarded 0 of 4') with a definition on hover.

### scene 6 · flat-table-hierarchy · mechanical

Clean, well-spaced and consistent in colour, but a designer has real notes: flat table typography (title, country and lot text all one weight/size), two button styles in one action column (five ghost 'View bids' plus one solid 'Compare & award') with the primary top-aligned against a four-line cell, and ~20% of the viewport left empty while the load-bearing lot quantities sit in ~13px text.

**Proposed fix:** Give the table a real hierarchy — set the solicitation title as the row's primary label at heavier weight and larger size, de-emphasise supporting cells, raise lot-line size, and unify row actions to one control style with the primary distinguished by placement rather than a second treatment; use the empty lower third for the recipient/eligibility panel rather than dead grey.

### scene 6 · act-claimed-not-performed · mechanical

The trace is pure camera work while the narration describes publishing a solicitation and a server-side eligibility decision, so a viewer cannot tell what was done — and the cursor's rest position sits on top of '35,000 cartons -> Damaturu', occluding the narrated quantity in the very row the scene scrolled to. Framing is otherwise correct: the hero row is fully in frame and settled.

**Proposed fix:** Make this scene perform what it narrates — click 'New solicitation', enter the four lots and publish so the new row appears, then move to the supplier-side view and hold on the solicitation's absence for an unqualified supplier — and park the hold's cursor off the LOTS cell so no lot quantity is occluded.

### scene 7 · award-ungated-by-evaluation · **FIXED** — #1071-era: award_lot now refuses while any submitted bid is unscored

The interaction model does not tell the user what a valid award is: eight identically-styled 'Award' primaries sit beside a price-only rank and an unscaled technical number with no threshold, no criterion, no evaluator identity and no scored-at date; and two lots ('35,000 cartons -> Damaturu', 'Kano-Maiduguri haulage') expose live Award buttons while their TECHNICAL cells are still unfilled 'Score' actions — the product permits the decision before the evaluation it displays exists.

### scene 7 · sticky-header-occlusion-persists · **FIXED** — fixed for the modal case via .card-head targeting

STILL-VISIBLE OCCLUSION CLASS: the sticky modal header hides the scrolled-to lot card's title and shaves its subtitle line, so the narrated table is unlabelled — '60,000 cartons RUTF delivered to Maiduguri' is out of frame and its '60,000 cartons -> Maiduguri, Nigeria · due Sep 15, 2026' subtitle is cut. Deduction rule: a frame element overlaps or occludes content (max 2). Compounded by left-aligned currency columns with 'USD' repeated per cell, cents on seven-figure values, and uniform brand-green on every technical score including 60 and 61 (decorative, not semantic). NOTE: the scroll-margin-top rule shipped for the earlier occlusion did NOT reach the lot cards inside this comparison modal

### scene 7 · lot-title-out-of-frame · **FIXED** — fixed: scene 7 targets .card-head

Order and settling are clean and both narrated lots are in one frame with all nine trace entries ok:true, but two rough edges remain: the scroll_to left the Maiduguri lot card's title above the visible region while that table is being narrated, and the synthetic cursor parks on a LOT VALUE cell, occluding 'USD 918,000.00' during the Djibo hold.

### scene 8 · award-ungated-and-unrecorded · **FIXED** — #1071-era: award_lot now refuses while any submitted bid is unscored

Two core interactions are incoherent for the product's own stated purpose: 'Score' and 'Award' sit as co-equal row actions on the un-awarded lots, so a first-time user can award a bid that was never evaluated; and the award itself is a single unguarded, irreversible click among four adjacent rows (~46px apart) with no confirmation, no rationale capture and no approver — after which the winning row is not marked in the table at all, leaving the award pill connected to its row only by name-matching. The award record carries no date, officer, value, justification or reference number.

### scene 8 · awardee-is-the-quietest-element · mechanical

Type hierarchy, alignment and spacing are clean and the host frame is intact, but the scene's load-bearing content — the awardee identity — is rendered as the quietest element on each card (a small pale grey-green pill at the right edge, lighter in weight than the LEAD TIME column header), and awarding vacates the action column leaving the right third of both awarded tables as blank gutter while the lot below keeps its buttons, so an awarded table reads as truncated rather than closed.

**Proposed fix:** Promote the awarded state: render it as a solid, high-contrast award block in the lot header carrying supplier, awarded value and award date, and reuse the vacated action column for the persistent row-level award marker plus a link to the award record. Drop trailing cents on lot values above six figures.

### scene 9 · handover-is-a-dead-end · mechanical

The narrated handover point is an interaction dead end: the 'Delivery schedule' panel is empty ('No consignments raised against this contract yet.') with no affordance to raise a consignment or confirm a plan, the contract offers no navigable link back to the award that created it (the relation the whole scene is about), the 'Active' state is undefined on a record where every quantity is zero, and OES-C-2026-NG1 / NG2 are indistinguishable in the pipeline table that feeds this modal (same supplier, same destination, adjacent IDs, no tender/award column).

**Proposed fix:** Give the contract record (a) a primary action in the empty Delivery-schedule state — 'Raise first consignment', seeded from the award's due date — so the handover is effectable rather than asserted; (b) a clickable award reference in the header that opens the immutable award record; (c) a tender/award column in 'Pipeline by corridor' so NG1 and NG2 are distinguishable without opening either.

### scene 9 · malformed-iati-identifier · **FIXED** — fixed: US-GOV-1-OES-C-… (duplicated org segment removed)

PRODUCT BUG (data correctness, found in the scene-9 adversarial pass): the IATI ACTIVITY field renders 'US-GOV-1-OES-OES-C-2026-NG2' — a duplicated organisation segment from concatenating the reporting-org prefix 'US-GOV-1-OES' with the contract reference 'OES-C-2026-NG2'. This is the identifier the product would publish to the IATI registry; a UNICEF/WFP reporting officer flags it as malformed on sight and a validator would reject it. Same redundant-composition class: APPROPRIATION reads 'FY2026 Emergency Food Security — Horn of Africa & Sahel · FY2026', with the fiscal year twice.

### scene 9 · broken-grid-and-backdrop · mechanical

Multiple product-side visual defects in one frame: the 'Drawn against' grid drops from four columns to two, leaving a dead void under IATI ACTIVITY and OBLIGATED with an uneven row-2 baseline caused by three- and two-line wraps; OBLIGATED USD 2,537,400.00 — the screen's whole subject — is set at body size identical to FUNDER and UNIT PRICE, so hierarchy is flat; '· AGAINST CONFIRMED DELIVERY ONLY', the most audit-relevant sentence, is orphaned micro-caps at likely-sub-AA grey overrunning its column edge; and the modal backdrop dims the top of the page but leaves the bottom rows ('Séno IPC 2 660 0 0% 660') at near-full contrast, bleeding through the overlay.

**Proposed fix:** Promote OBLIGATED to a hero figure with UNIT PRICE x QUANTITY shown as its derivation beneath it; make 'Drawn against' a stable two- or three-column definition list so wrapped values cannot ragged the next row or leave a void; raise the disbursement qualifier to body-size sentence case ('Disbursed against confirmed delivery only') and drop the leading middot; and fix the modal backdrop to cover the full document height at one uniform opacity.

### scene 9 · row-in-context-never-seen · mechanical

The contract modal is fully in frame, centred and settled, and every trace entry returned ok:true, so there is no mis-framing or failed action. The rough edges are timing and context: the scene's final hold, whose own note promises 'the consignments it is moving on', lands on 'No consignments raised against this contract yet', and the 'Pipeline by corridor' table the scene deliberately scrolled to and clicked is entirely hidden behind the modal it opened, surviving only as clipped unlabelled numbers at the right edge — so the viewer never sees the row-in-context to record relation the narration depends on.

**Proposed fix:** Re-script: after scroll_to 'Pipeline by corridor', add a hold on the highlighted OES-C-2026-NG2 row (fully framed, before any modal) while the narration says 'the same record the tender produced', THEN click through to the contract; and re-time the closing hold so the 'delivery schedule' beat lands on a populated schedule rather than the empty-state line.

### scene 1 · undefined-alarm-colour · options

Host chrome is intact and type hierarchy is intentional, but two deduction rules fire: real content overflows the viewport (the Solicitations panel's first data row, 'Sudan Corridor Logistics 2026', is sliced mid-glyph at the frame edge) and the amber accent on two of four KPI tiles is decorative rather than semantic because no threshold defines it. Also '1 Open EOI rounds' is ungrammatical, '2 total' is an unlabelled second number, uneven queue row heights from wrapping chips, and a ~200px void under the coverage panel.

**Proposed fix:** Define the amber accent semantically — state the threshold it encodes and expose the definition through the existing 'i'-bubble pattern — or remove it; fix the '1 Open EOI rounds' pluralization and label '2 total' with its unit; balance the two-column grid so the coverage panel and the queue share height; and let an in-flight solicitation reach the first screen by collapsing awarded history behind a filter. The colour decision is a genuine either/or.

### scene 1 · narrated-artifact-clipped · options

The frame is settled and honest (wait_for .keyfigures ok:true, no spinner, no dead end, camera-only trace correct for a role:overview scene), but the scene declares it shows 'the live solicitations' and the narration reaches 'then tenders drawn from it', while the Solicitations panel is clipped to a header row plus a half-rendered line and its only live row sits six rows down, entirely off-screen. That panel would fit a viewport if the scene scrolled to it — the fits-but-mis-framed case.

**Proposed fix:** Scene-scripting fix: after the hold on the key figures, add a scroll_to on the Solicitations panel with a settle-hold timed to the 'then tenders drawn from it' clause; alternatively split the overview into two beats (registry health, then tenders in flight) with a scroll between them.

### scene 4 · decision-dead-end · options

The post-decision interaction is a dead end a first-time reviewer would stumble on: the decided row silently disappears, no confirmation names what was recorded, and there is no reachable record, history, or correction path for a verdict that changes an organisation's eligibility — compounded by a two-step model (Qualify/Reject set local state; only 'Record decision' commits) whose committed result the screen never confirms. The sole confirmation is a transient five-word toast marooned ~500px below and ~900px right of the row it concerns, naming no supplier, category, verdict or expiry.

**Proposed fix:** After 'Record decision', keep the application reachable: move it to a Decided tab/section with per-category verdicts, granted and expiry dates, reviewer name and timestamp, and a link to the Qualification rows created. Then either add an 'undo within N minutes' window or an 'amend decision' affordance (both are defensible; pick one) so a mis-click is correctable and the change is itself logged.

### scene 4 · truncated-rail-and-void · options

Multiple product-side presentation gaps in one frame: the left rail's panel background terminates with a hard square edge mid-page (~y=340) while the canvas continues below it, reading as an unfinished shell; roughly 45% of the viewport is empty grey with all content top-weighted (content ends ~y=405 of 720); the only confirmation is orphaned in the far bottom-right; and three identical filled primary 'Review' buttons give the table no action hierarchy. The table's own type and spacing are clean, which keeps this off 1.

**Proposed fix:** Run the side rail full height as a proper two-column grid, or give it real card treatment (either is defensible — pick one); let the queue card fill the available height or add the rounds/summary content the page title promises so the frame is not half void; replace the corner toast with an inline confirmation where the decided row was, naming supplier and outcome; and demote two of the three 'Review' buttons to secondary or make the row itself the primary target.

### scene 6 · road-transport-unit-inconsistency · **FIXED** — fixed: the Sudan lot reads cartons everywhere

Cross-scene data-coherence note (orchestrator observation, corroborated by the scene-6 judge's 'the hero row's LOTS cell mixes units'): road-transport lots are denominated two different ways in the same list. 'Sudan Corridor Logistics 2026' (category Road transport) carries '40,000 cartons -> Khartoum', while the Q3 tender's road-transport lot carries '6 truck-months -> Maiduguri'. The prior fix that made the Sudan lot read 'cartons' is applied and consistent within that row, but it now reads as a haulage tender priced per carton beside a haulage lot priced per truck-month, and the row gives no lot-to-category mapping — the very attribute eligibility is decided on.



## 31 July judge pass — new PRODUCT findings (mechanical unless noted)

Harvested from oes-supply-base-2026-07-31-003, oes-partner-pipeline-2026-07-31-001,
oes-command-centre-2026-07-31-001, oes-money-to-child-2026-07-31-001. Full detail
with per-scene routing in each run dir's `design_findings.json`. Scripting-level
findings were fixed in the recipes the same day and are not repeated here.

### money-to-child · Sankey labels unreadable · mechanical

Partner names and amounts ('$2.0M', '$1.9M', '$872k', '$128k') render directly on
the dark navy nodes — effectively unreadable and partially clipped, in the funding
chart's load-bearing middle column.

**Proposed fix:** contrast-safe labels inside the node (white on dark) or offset
clear of the rectangle with collision avoidance.

### money-to-child · InfoNote popover truncates its own method · mechanical

The cost-per-child method popover clips its text mid-sentence ('…CONFIRMED
deliveries only, so') with no expand affordance, while occluding the paragraph
beneath it — the scene's hero disclosure is illegible at the moment it opens.

**Proposed fix:** size the infonote body to content (drop/raise max-height) and
offset it below its trigger line.

### money-to-child · DELIVERED renders 'N / — cartons' · mechanical

All four contract rows show a ratio with a missing denominator.

**Proposed fix:** populate the contracted-carton denominator, or render a plain
count until it exists. Also draw the per-contract stage bars in the labeled
OBLIGATED → DISBURSED → DELIVERED order with empty tracks for zero stages.

### supply-base · qualification arithmetic · options

'18 months' grants Jan 22 2028 from Jul 31 2026 (540 days, not calendar months →
Jan 31), and the pass silently outlives the UNICEF certificate it visibly rests
on (expires Jul 4 2027). Pick: calendar-month arithmetic, and either cap the
expiry at the load-bearing certificate's or flag re-verification at cert expiry.

### supply-base · pre-commit decision phrasing · mechanical

The decision row asserts 'qualified until Jan 22, 2028' in the present tense
before Record decision commits. Phrase prospectively until committed.

### supply-base · eligibility is invisible · mechanical

Server-side solicitation scoping has no positive artifact: add a per-solicitation
'Visible to N qualified suppliers (RUTF)' indicator with an 'i' defining the rule.

### command-centre · small-copy cluster · mechanical

'1 days behind' (twice); the top exception card repeats its children count
verbatim in title and sub-line; partner-shortfall recommendation names expedite
with no expedite affordance on that card type; absorbed-delay cards recommend
actions their own text says are unnecessary; the same fact in two date formats on
adjacent lines; IPC legend still occludes the Mapbox attribution; dead white band
under the network map.

### partner-pipeline · calendar legend and criteria · mechanical

The covered/at-risk/uncovered color key lives at the end of a prose paragraph
below the fold, and the amber-vs-red criterion is undefined on screen. Inline
three-chip legend on the card header; define the bands; move prose to 'i'.

### partner-pipeline · MUAC chart row consistency · mechanical

Band labels, visit-count and delta annotations render on the highlighted row
only; sparklines carry no time axis; '2 visits over 1 week' wraps one word per
line; the last row clips mid-badge.

---

## Fourth judge pass (31 July 2026, post-fork-resolution) — 29 scenes, four independent judges

Every arc performed every narrated act (296/296 actions ok) and every
deterministic lens passed, so this pass reached a deeper class of finding than
the previous ones. **The rubric was also run harder than the third pass** (each
judge was told to list at least eight candidate flaws per scene and to start
from 3), so per-scene numbers are NOT comparable with the third pass's — what is
comparable is that none of the third pass's fixed findings were re-reported.

### FIXED IN THIS ROUND

Three of them were regressions introduced by the previous round's own fixes,
which is the honest cost of that round and the reason a judge pass is worth
running after every one:

- **The method popover outlived its subject.** Making it `position: fixed` (to
  stop `.card`'s `overflow: hidden` clipping its text) pinned it to the
  VIEWPORT, so a note opened in one scene floated over the next three — in the
  command-centre arc a note about Borno's caseload sat over an unrelated expiry
  row and then over the closing sentence of the whole narrative. Now dismissed
  by any scroll, Escape, or outside click.
- **The popover covered the data below it.** Anchored below its trigger
  unconditionally, a note opened near a card's foot spilled into the next card
  and hid the very figures it explained. Now flips above when there is no room.
- **The page scrolled sideways.** `.content` is a flex item, so its default
  `min-width: auto` let a wide table grow the column, grow the shell, and scroll
  the DOCUMENT — taking the wordmark and left nav off the left edge of every
  funder frame ("eration End Starvation"), and letting the map bleed over the
  sticky header. `min-width: 0` hands horizontal scrolling back to the table's
  own wrap. One root cause behind roughly a dozen reported symptoms.
- **The unit ladder did not reproduce itself.** 166 MT / 13.8 kg = 12,029, and
  the next rung read 12,000 — on the card whose whole claim is that every step
  can be checked. Now one decimal (165.6 MT), so the division closes.
- **The caseload method did not reach its own number.** The note stated the
  formula and stopped, evaluating to the MONTHLY caseload while the cell beside
  it showed the caseload summed over the response window. It now evaluates
  explicitly and says which figure it evaluates to.
- **"Still uncovered" contradicted "Reached a child".** The column is
  requirement minus POSITIONED supply, so it read as a treatment gap while the
  column two places left said 7.9% had reached a child. Renamed on all three
  surfaces to "Children with no supply positioned".
- **A past-deadline tender read as open.** A solicitation three days past its
  bid deadline still showed "Published". Now derives "Bidding closed" from the
  date, leaving the lifecycle field alone.
- **An empty review queue ate the fold.** Two judges flagged it independently:
  the largest panel on the establishing screen held one sentence, pushing the
  only live tender below the fold. Collapses to one line when clear.
- **Narration vs render, four places.** command-centre s2 named three ingestion
  tiers and a Port Sudan-to-El Fasher leg; the screen shows four legs all
  reading "Phone check-in" and no such leg — re-narrated to the honest worst-case
  corridor, which is a stronger scene. partner s2's covered exemplar (Monguno)
  was row 10, below the fold — now Dikwa, which is in frame. partner s6 called a
  batch that arrived in April "last month" and claimed workers "screen every
  child" while the card's own caveat says outcomes exist for 22 of 218 — the
  product was more honest than the voiceover, now corrected to match it.
- **Three canonical frames were captured before their payoff.** command-centre
  s8 snapshotted the closed row and only then scrolled to the headline the
  closing line is about; money s4 opened the scope marker, closed it, then
  snapshotted the table; money s6 snapshotted inside the batch modal, which
  covers the card carrying every figure the narration names.

### STILL OPEN — highest value first

These are real and unfixed. Several are design decisions rather than defects.

1. **No lapse view in the registry** (supply-base s5, `redesign`). The claim is
   "a lapsed certification drops a supplier out without anyone maintaining a
   list", and a registry that by construction excludes lapsed suppliers cannot
   show it. Needs a "Lapsed since" view naming supplier, category, lapse date and
   the certificate that expired — the artifact an inspector would ask for.
2. **Server-side eligibility still cannot be proven on screen** (supply-base s6).
   A count cannot distinguish server filtering from CSS hiding. The honest proof
   is the supplier-side absence, which needs a mid-scene persona switch
   (dev-login, 403 on prod by design). Also: the reach count does not MOVE when a
   qualification is granted, because the supplier was already qualified in the
   seed — either seed them unqualified so the count increments on camera, or stop
   implying the count moved.
3. **Per-category evidence blocks are missing** (supply-base s3/s4). A decline
   rests on an absence, and an absence is invisible: therapeutic milk has no
   commitment block at all, so the reject cannot be defended from the screen. An
   explicit "no capacity declared, no plant listed, no approval on file" block is
   far stronger than a blank. Reviewer notes should also be required on a reject.
4. **Qualify and Reject render the same green** (supply-base s4). Two opposite
   verdicts, identical colour — the "one supplier, two answers" contrast is
   visually erased on the frame that exists to make it.
5. **The exception queue cannot be read as a ranking** (command-centre s1).
   Roughly 1.5 cards fit the viewport at ~215px each, and there is no rank
   ordinal or magnitude encoding, so the ordering is implicit. A compact two-line
   row would show four or five at once.
6. **The event log has one entry** (command-centre s4). "Append-only, derived
   status" cannot be demonstrated by a single row, and the loudest element on the
   card is a write button. Needs a consignment with real history, including one
   entry superseding another.
7. **The map draws no flows on the government view** (money s4) and its only
   marker sits on Lagos while every destination is north-east.
8. **MUAC charts are individually auto-scaled** (money s6, partner s6), so five
   different trajectories render as five near-identical curves — which defeats
   the only reason to stack them. Needs a shared y-domain and a real axis.
9. **Three delivery totals on one page** (money s1): 47,000 cartons in the table,
   33,000 "under contract", 58,251 on the closing card, with no bridging note.
10. **Award records carry no rationale, approver or reference** (supply-base s8),
    and losing bidders get no explicit "not awarded" state.
11. **Projector-test failures are systemic**: in-chart labels at 7-8px, field
    labels at 9-11px uppercase grey, method notes at 11px. The figures that
    carry each claim are consistently the smallest text in frame.
12. **The seeded world has visible repeats** — each site's two consignments carry
    identical carton counts, and 0 of 14 applications were ever rejected, which
    reads as a rubber stamp on the screen that sets up the review scenes.
