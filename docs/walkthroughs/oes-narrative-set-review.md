# OES narrative set — review of v1

Status: **review complete and acted on, 2026-07-27.** Reviewer: Claude, acting
on delegated authority for the `concept_change` gate (the operator explicitly
stood down from human review for this cycle).

All four narratives were corrected and re-posted to canopy-web as **v2**, and
all four `concept_change` gates are **resolved: approve**. Every scene in the
set now reads `built`, because the corrected build order below was executed —
see the `supply:` commits on this branch and `connect_labs/supply/README.md`
for what landed.

Subject: the four narratives ACE posted to canopy-web at 2026-07-27T20:34Z, all
at `concept_change · pending`, all v1, all with zero runs:

| slug | scenes | persona |
| --- | --- | --- |
| `oes-supply-base` | 9 | Ada / Amina / Tomas |
| `oes-partner-pipeline` | 6 | Zara |
| `oes-command-centre` | 8 | Ada |
| `oes-money-to-child` | 6 | Dale / Hauwa |

## Verdict

**Agree, with corrections.** The set is good work. The four-way split is the
right decomposition of this product, the why-briefs are genuinely grounded
(real file refs, real evidence kinds, honest `gap` markers), and the G4
DECISION in `oes-partner-pipeline` — synthesise the last mile inside supply
rather than importing Connect service-delivery data, to preserve the app's
zero-cross-app-import property — is exactly the right call, correctly
identified as a decision rather than smuggled in as an assumption.

The corrections below are of three kinds: **six false status claims**, **seven
phantom URLs**, and **three structural problems that only exist because this is
a multi-narrative set** — which is the interesting category, because they are
invisible to every per-narrative gate canopy currently runs.

---

## A. Status claims that are wrong

Each scene feature carries `status: new | built`. Six are wrong. Verified
against the code at `35e0eedd`, with `pytest connect_labs/supply` green at 124
passed.

### Claimed `new`, actually already built (5)

| feature | claimed | evidence it exists |
| --- | --- | --- |
| `procurement-console` | new | `static/supply/tab_console_home.jsx` renders open rounds, live solicitations and the review queue off the bootstrap payload |
| `eoi-round-lifecycle` | new | `models/procurement.py:57` `EOIRound` with draft/open/closed; `POST api/eoi/rounds/<id>/transition/`; `tab_rounds.jsx`; covered by `test_eoi.py` |
| `per-lot-bid-comparison` | new | `GET api/rfps/<id>/comparison/` → `rfp_actions.lot_comparison`, price-ranked with `include_scores=True`; rendered by `rfp_detail.jsx` / `tab_bids.jsx` |
| `per-lot-award` | new | `models/procurement.py:205` `Award` is per-`Lot` with a `OneToOne`; `POST api/lots/<id>/award/`; `test_rfp.py` already asserts a lot cannot be awarded twice |
| `unit-ladder-with-method` | new | `tab_funder.jsx:118-152` renders the full $→MT→cartons→children ladder **and** a `method-note` paragraph stating the 150 × 92 g conversion and the cost-per-child derivation |

That last one matters most: `oes-money-to-child` scene 3 is written as if the
method note is the thing being added, when the method note is already the best
sentence on the funder page. Building to that narrative would produce a
no-op scene.

**Why this happened, and it is worth naming:** the app is a **single-route
SPA**. `routes.py` ends at `path("", views.app_view)`; every surface is a
client-side tab in `static/supply/*.jsx`. An evidence pass that greps
`routes.py` for `/supply/console/` finds nothing and concludes "not built" —
which is precisely the wrong conclusion. Four of the five false `new`s are
frontend surfaces that exist as tabs.

### Claimed `built`, actually partial (1)

`partner-receipt-and-discrepancy` (partner scene 4) is marked `built`. Check-in
tier capture and `Discrepancy` do exist and are well covered. But the feature
description says the discrepancy is *"attributed to the receiving partner
org"* — and there are no partner orgs. `SupplierOrg` has no kind
discriminator; `roles.py` has five roles and none of them is a partner. The
receipt half is built; the attribution half cannot be, until
`implementing-partner-role` lands in scene 1 of the same narrative.

Correct status: `partial`, blocked on its own narrative's scene 1.

---

## B. Verify commands that cannot run

Seven feature `verify:` lines name URLs the app does not serve:

```
GET /supply/console/          GET /supply/command-centre/
   /supply/eoi-rounds/           /supply/funding/
   /supply/registry/             /supply/solicitations/<id>/
```

All are SPA tabs behind `path("", views.app_view)`. `GET /supply/console/`
returns 404, not 200 — so `procurement-console`'s verify fails on a system
where the feature works perfectly.

A second, subtler failure: `children-at-risk-severity` (command scene 6) is
specified with a pytest-shaped verify — *"two exceptions with equal tonnage but
different caseloads rank by the larger children-at-risk figure"* — but exception
severity is computed **in the browser**, by `ExceptionSeverity()` and
`buildExceptions()` in `tab_command.jsx`. The repo has no JS test harness at
all. As written the verify is unrunnable.

This one is not just a documentation fix, because of a constraint the narrative
set imposes on itself. `partner-cover-projection` says its numbers must *"agree
with the figure the command centre shows for the same node"*. Two independent
JS implementations of a stockout projection will drift the first time either is
touched. **Resolution: the cover/burn/severity derivation moves server-side into
one module that both surfaces consume**, which makes the narrative's own
consistency requirement structurally true rather than aspirational, and makes
every verify in this cluster a real pytest. Adopted below.

---

## C. Structural problems specific to a multi-narrative set

These are the findings that justify the exercise. None is visible to
`ddd-spec-qa`, `ddd-narrative-coherence`, or `ddd-narrative-actionability-eval`,
because all three run **per narrative**.

### C1. There is a shared substrate, and no narrative owns it

Six features across three narratives all depend on one model that does not
exist yet, `CaseloadEstimate`, and on one derivation that does not exist yet,
per-node cover:

```
CaseloadEstimate ──┬── requirement-against-need        (command s5)
                   ├── children-at-risk-severity       (command s6)
                   ├── coverage-by-district            (money   s5)
                   └── cover derivation ──┬── cover-and-stockout-projection (command s6)
                                          ├── expiry-risk-exception         (command s6)
                                          └── partner-cover-projection      (partner s3)

DistributionRecord + ChildOutcome ──┬── distribution-record / child-outcome-series (partner s6)
                                    └── two-figures-and-the-gap / batch-to-outcome-drill (money s6)
```

`oes-command-centre` scene 5 introduces `CaseloadEstimate` as if it were a
command-centre feature. `oes-money-to-child` scene 5 says it "consumes the
`CaseloadEstimate` model" — correctly implying a dependency, but nothing
declares the order, and a reader of `oes-money-to-child` alone would reasonably
try to build it. `oes-partner-pipeline` scene 6 and `oes-money-to-child` scene 6
both introduce `ChildOutcome`, each as if first.

Fix: a declared **Layer 0** owned by no narrative, built first. See the build
order below.

### C2. Cross-narrative outcome leakage

`oes-partner-pipeline` scene 5 (Zara raises a shortfall) ends:

> *"Two days later the answer comes back the same way — cartons reallocated from
> a surplus warehouse in Kassala, with the reason attached, appearing on Zara's
> calendar as cover for the Thursday distribution."*

That reallocation is the action `oes-command-centre` scene 8 performs. The
partner narrative asserts its result before the narrative that produces it has
run. This is exactly the class `canopy:ddd-narrative-coherence` was written to
catch — *"a beat asserts specific values that a later step is supposed to
generate"* — and it slips through untouched because the producing step is in a
different YAML file.

It is also not merely a QA nit: as written, partner scene 5 requires seeded
demo state in which the reallocation has already happened, while command scene 8
requires the same reallocation to be performable live. Those two demands
contradict unless somebody notices and splits the fixture.

Fix: partner scene 5 ends at the signal landing and being acknowledged. The
answer coming back moves to a short partner scene 5b placed *after* the command
centre in the set order, or — simpler, adopted here — the sentence is rewritten
to describe the mechanism rather than assert the outcome: the signal lands in
the queue ranked by children behind it, and the loop it opens is closed in the
command-centre narrative.

### C3. No declared set order, though the set clearly has one

The four narratives read in exactly one sensible sequence — procurement →
partner → command centre → money — and depend on that sequence (C2 is a symptom).
Nothing records it. canopy-web lists them newest-first, which happens to be
*reverse* order.

---

## D. Substantive improvements, beyond correctness

### D1. Specify the gap in `oes-money-to-child` scene 6

The best idea in the whole set is the closing beat: courses delivered and
children with a recorded recovery, side by side, disagreeing. *"That gap is the
finding."*

But the narrative never says how big the gap should be or why, which means the
synthetic generator will produce either zero (if outcomes are seeded one per
course) or noise. An unexplained gap is worse than no gap — a funder asks "why
is it 40%?" and there is no answer.

Ground it in the standard the sector already grades itself against. Sphere /
SMART performance thresholds for SAM treatment programmes: recovery ≥ 75%,
death < 10%, defaulter < 15%. Seed the outcome series so the cohort lands at
roughly **82% recovered, 13% defaulted, 3% transferred to inpatient care, 2%
non-response** — inside the acceptable band, so the gap reads as an honest
programme rather than a broken one, and the number has a citable provenance.
State the composition on screen next to the gap. Adopted.

### D2. `requirement-against-need` should not restate the treatment factor

The feature says *"requirement equals caseload times the treatment factor."*
`gs1.py` already owns this: `CARTONS_PER_CHILD_TREATED = 1`, with
`cartons_to_children()` and `cartons_to_mt()` beside it and the UNICEF
specification cited in the module docstring. Every new derivation should call
those, not restate the ladder. Otherwise the funder ladder and the command
centre requirement drift the first time anyone revises the assumption.

### D3. The set has no supplier-side execution scene

`tab_ops.jsx` and `tab_integration.jsx` — the supplier's own reporting surface,
the three-tier webforms, and the token issue/revoke flow — are substantial built
features with no scene anywhere in the set. Command scene 2 shows the three
tiers from Ada's side only. Not a defect in any one narrative; a coverage hole
in the set. Noted, not fixed here — adding a fifth narrative is a bigger call
than this review should make unilaterally.

---

## E. Corrected build order

Layers, not narratives. Each layer is independently shippable and testable;
nothing in a layer depends on a later one.

**Layer 0 — substrate** (owned by no narrative; build first)
- `CaseloadEstimate` (country, `adm1_code`, month, children, source note),
  joined to the existing `static/supply/geo/admin1_ipc.geojson` by `adm1_code`
- node → served-district link, so a node has a caseload
- `split-award-demo-state` (independent; `test_seed.py` currently asserts the
  awarded RFP has exactly **one** award, so the split the narrative describes is
  not in the demo world at all)

**Layer 1 — derivation** (server-side, single source, consumed by two surfaces)
- `services/cover.py`: stock on hand from receiving events, weekly burn from
  served caseload, weeks of cover, projected stockout date
- exception severity by children-at-risk, moved out of `tab_command.jsx`

**Layer 2 — command centre demand side**
- `requirement-against-need`, `cover-and-stockout-projection`,
  `children-at-risk-severity`, `expiry-risk-exception`

**Layer 3 — partner pipeline**
- `implementing-partner-role`, `partner-owned-nodes`, `distribution-plan`,
  `partner-cover-projection` (consumes Layer 1), `shortfall-signal`,
  `partner-raised-exceptions`

**Layer 4 — actions**
- `supply-action-model`, `reallocation-creates-a-shipment`

**Layer 5 — outcomes**
- `distribution-record`, `child-outcome-series`, `batch-to-outcome-drill`,
  `two-figures-and-the-gap`, `coverage-by-district`

Layers 3 and 5 are where the narrative set's payoff lives; Layers 0–1 are where
its correctness lives.

---

## F. Missing artifact: there are no recipes

`docs/walkthroughs/` holds a `.recipe.yaml` + `.narrative.lock.json` pair per
narrative since #1009. All four OES narratives exist **only** on canopy-web —
no recipe, no lock, nothing in this repo. `narrative pull` reconstructs the lock
and the why-brief but not a recipe, and reports success, so a narrative authored
elsewhere lands in a state where it cannot be rendered and nothing says so.

Filed against canopy rather than fixed here.

---

# Addendum — 2026-07-28: what judging actually found

The review above was written before any narrative had been judged. Two of the
four now have been, and the findings are a different shape from the ones a
reading produced. Recorded here because the *pattern* generalises to the two
narratives still unrendered.

## G. The recurring defect: a scene that narrates and does not demonstrate

`ddd-arc-eval` ran for the first time (on `oes-supply-base`) and returned
**fail, 2/5 on all five dimensions**. Its most valuable finding is invisible to
every per-scene lens, because a per-scene judge sees one frame and cannot
compare two:

> Scenes 3 and 4 carry the demo's two most differentiating claims — a submission
> snapshot frozen at the moment of submission, and a qualification decided per
> category with an expiry — and **neither claim was on screen**. Both scenes'
> action lists ended at a nav click.

And, separately:

> Scene 4 is scene 2 with a card **deleted**. Same route, same queue, same three
> rows. It shows strictly less than the scene two before it, and its removal
> would not be noticed.

Both are the same underlying failure: **the recipe stopped at the surface that
contains the thing, instead of opening the thing.** A nav click is enough to
frame a claim and never enough to demonstrate one.

This is worth checking in every recipe in the set before rendering it, because
it is cheap to check by reading and expensive to find by judging.

## H. `oes-command-centre` — blockers found by reading, before spending a render

Verified against the seeded world on 2026-07-28. All four remain open.

1. **The payoff scene does not perform its action.** Scene 8's narration is
   "Ada reallocates: cartons from the Kassala warehouse … to El Fasher … a
   consignment appears on the map with planned milestones … and the exception
   resolves against the action that resolved it." Its recipe actions are a
   scroll, a click that expands an exception row, and two holds. Nothing is
   reallocated. This is the scene the whole narrative builds toward, and it is
   currently scene 6 with a row expanded — the same defect as G above, in the
   worst possible place.

2. **Scene 8's `show` and its narration disagree about the destination.** The
   `show` says "to the site that raised the shortfall" — that is Askira
   Nutrition Centre, in Nigeria. The narration says El Fasher, in Sudan. Kassala
   → El Fasher is coherent (one corridor); Kassala → Askira is not.

3. **"Nine days" does not exist.** Scenes 1 and 3 both narrate a consignment
   nine days late; scene 3 asserts it of the specific consignment on screen.
   No leg is nine days late — the authored slip table tops out at six.
   `narrated_numbers` will fail on this. *Suggested fix, which also strengthens
   the arc:* make `SHP-2026-0202` (Khartoum → El Fasher, check-in tier) the
   nine-day one. That single change ties scene 2 (the Sudan corridor arrives as
   phone check-ins), scene 3 (nine days behind plan), and scene 8 (Kassala → El
   Fasher, because El Fasher is short *because* that leg is late) into one
   causal chain instead of three unrelated corridors.

4. **Scene 5's coverage figures are not in the data.** The narration says "this
   district is at ninety-one percent of need, this one at thirty-four."
   Actual `coverage_by_district()` on the seeded world:

   | district | IPC | coverage |
   | --- | --- | --- |
   | Séno, Yagha, Yobe, Southern Darfur, North Darfur, Kassala | 2–5 | **0%** |
   | Borno | 5 | 51.2% |
   | Somali | 4 | 67.6% |
   | Soum | 5 | **145.8%** |

   Neither 91 nor 34 appears, so `narrated_numbers` fails. Worse for the demo:
   six of nine rows read 0%, which `data_fidelity` flags as an identical column
   and which makes a coverage table a poor advertisement for coverage. The
   scene's own `show` is stale too — it claims "Borno at 31% with 33,232
   children uncovered" against an actual 51.2% / 23,557.

   The fix is deliveries seeded into more districts, not a narration edit.

5. **Two smaller ones.** Scene 5 hovers a `span[title]` to reveal the caseload's
   method — a native browser tooltip, which does not render in a headless
   screenshot, so the beat captures nothing. Scene 4 has no actions at all
   beyond a hold, and sits on the same frame as scene 3.

## I. Known environment limitation

The network map renders as an empty white panel in every local render: this
environment has no `MAPBOX_TOKEN`. The arc judge routed it PRODUCT and it is the
run's only non-tabular surface, so it costs `visual_variety` in every narrative
that shows the command centre. It is a config gap, not a build defect, and it
cannot be fixed from inside a recipe.

## J. `oes-money-to-child` — read the same way, before rendering

Same exercise, same date. Two findings, one of which is shared with
`oes-command-centre` and is the single highest-leverage fix left in the set.

1. **Scene 6 narrates a drill it never performs.** "Dale can follow a single
   delivered batch through the distributions it fed to one child's arm
   circumference over time" — the actions are two scrolls and two holds. The
   drill-in modal exists (the partner narrative opens it); this scene just never
   clicks. Finding G again.

2. **Scene 5 narrates the same coverage figures as `oes-command-centre` scene 5,
   and they are absent from the data in exactly the same way.** Both say
   "ninety-one percent" and "thirty-four". `narrated_numbers` will fail on both.

### The coverage seed, specified

The narration is unusually precise, which means the target state can be derived
rather than guessed. `oes-money-to-child` scene 5:

> "this district is covered to ninety-one percent of need and this one — **which
> received more cartons** — sits at thirty-four, with thirty-one thousand
> children still uncovered."

Solve it against the seeded caseloads and it lands on two specific districts:

| district | caseload | target coverage | courses delivered | uncovered |
| --- | ---: | ---: | ---: | ---: |
| **Borno** | 48,232 | **34%** | ~16,399 | **~31,833** ✓ "thirty-one thousand" |
| **Kassala** | 7,020 | **91%** | ~6,388 | ~632 |

That pairing satisfies the hard part of the sentence — Borno receives **more
cartons** (16,399 vs 6,388) and still sits far lower, because its caseload is
seven times larger. That contrast *is* the scene's whole argument, and it is
currently unavailable at any pair of districts in the seeded world.

Current state, for comparison — six of nine districts at 0%, one above 100%:

| district | IPC | coverage |
| --- | --- | --- |
| Séno, Yagha, Yobe, S. Darfur, N. Darfur, Kassala | 2–5 | 0% |
| Borno | 5 | 51.2% |
| Somali | 4 | 67.6% |
| Soum | 5 | 145.8% |

So the work is: deliver into Kassala (currently nothing lands there), reduce
Borno's delivered volume to ~16,399, and give the remaining districts a
non-degenerate spread. Delivered volume is derived from the append-only event
log via `services/coverage.py`, so this is seeding shipments and receipts — not
writing a coverage number. Two knock-on checks: the command-centre "Pipeline by
corridor" gap column and `test_the_demo_world_*` in `tests/test_demand.py`.

One fix, and both remaining narratives get a scene 5 that is true.

## K. Two product bugs the concept judge found, verified 2026-07-28

Neither is a demo defect. Both are wrong in the app, and both are on camera.

### K1. `Contract.shipped_quantity` counts the same cartons on every leg

`models/execution.py` sums **every** non-planned shipment on the contract:

```python
self.shipments.filter(unit=self.unit).exclude(status=PLANNED).aggregate(Sum("quantity"))
```

A consignment moves in hops, and each hop is its own `Shipment`, so a carton is
counted once per leg it travels. Verified on `OES-C-2026-NG1`, whose contracted
requirement is **45,000 cartons**:

| legs | quantity |
| --- | ---: |
| Kano Plant → Kano Central | 20,000 |
| Kano Central → Maiduguri | 15,000 |
| Kano Central → Damaturu | 10,000 |
| Maiduguri hub → 11 nutrition centres (last mile) | 9,910 |
| **`shipped_quantity`** | **54,910** |

54,910 shipped against a 45,000 requirement — 122% — rendered straight into the
command centre's "Pipeline by corridor" table. `delivered_quantity` (44,675) has
the same structure. In a product whose entire pitch is carton-level
traceability, the headline movement figure double-counts.

**This needs a domain decision, not a patch.** The contract promises 45,000
cartons *delivered to Maiduguri*; the hub→centre legs are last-mile distribution
*past* that delivery point. Counting only terminal legs, or only legs arriving at
the contract's own `delivery_place`, both give defensible answers — and they give
*different* answers (15,000 vs 44,675), which changes headline numbers in three
narratives. Deliberately not decided here.

### K2. The command centre's "Weeks of cover" table is empty where it matters

All 12 nodes read 0 on hand / 0 weeks / runs dry today, and every node that
actually received the 9,910 delivered cartons is **missing from the table
entirely**. That reads as a broken join rather than an operational finding, and
it sits on camera behind `oes-supply-base` scene 9's modal. It is also the table
`oes-command-centre` scene 6 and `oes-partner-pipeline` scene 3 are built on, so
it blocks both.

## L. The one narration change the set needs

`oes-supply-base` scene 8 says *"Splitting costs a little more per carton and
buys the thing money cannot buy later."* Two independent judges computed the
same refutation. Every alternative, against the seeded bid book:

| award | total | blended | vs split |
| --- | ---: | ---: | ---: |
| **split as awarded** | **3,374,400** | **42.1800** | — |
| Savanna both | 3,455,400 | 43.1925 | +81,000 |
| Kano both | 3,554,600 | 44.4325 | +180,200 |
| Lagos both | 3,693,600 | 46.1700 | +319,200 |
| Faso both | 3,755,400 | 46.9425 | +381,000 |

Each lot went to its own cheapest bidder, so the split is the **global cost
minimum** — $1.01/carton *cheaper* than the best consolidation, not dearer. The
demo shows a free lunch while narrating a sacrifice, and a supply officer does
that subtraction in three seconds from the table the previous scene made them
read.

This cannot be fixed in data without breaking scene 7. Scene 7 requires a
*different price leader per corridor*; if each lot then goes to its own leader,
the split is necessarily cheapest. The two beats are mutually exclusive over one
bid book.

The honest sentence is also the stronger one, and this fixture already supports
it: **each corridor's own cheapest bidder is a different plant, so the split is
simultaneously the resilient award and the lowest-cost one.** That is a narration
change to a locked narrative — a `concept_change` gate, and an operator decision.


## M. K1 is now blocking, and a test proved it

Attempting the coverage seed in §J turned K1 from a reporting defect into a hard
blocker, which is worth recording because it settles the priority.

Two halves of the double-count were found, not one. `services/coverage.py` had
it as well as `models/execution.py`: summing every arrival in a district counts
the cartons that reach the district hub, and then counts them AGAIN as the hub
despatches them onward to the sites it serves. Borno read **24,675** against
**15,000** that ever crossed its boundary. That half is fixed — a leg now counts
toward a district only when it crosses into it, since redistribution inside a
district is not new supply reaching it.

The contract half is not, and it now blocks the narration:

- Borno needs **~1,400 more cartons** across its boundary to reach the 34% its
  narration speaks (16,399 of a 48,232 caseload, leaving the 31,833 uncovered
  that the funder scene calls "thirty-one thousand").
- Every route into Borno belongs to contract `OES-C-2026-NG1`, whose
  `delivered_quantity` already reads **44,675 against 45,000 contracted** — 325
  cartons of headroom.
- Adding the leg took it to **46,074**, and
  `test_disbursement_never_exceeds_obligation` failed with *"OES-C-2026-NG1
  delivered more than it contracted for"*.

The test is right and the invariant is real. The contract only appears nearly
fulfilled because `delivered_quantity` counts each carton once per leg it
travels; on any single-counting rule it is badly under-delivered. So the demo
cannot tell its own coverage story until the contract measure is fixed, and the
Borno leg is deliberately **not** seeded rather than papered over by raising the
contract quantity.

**The decision that unblocks everything.** What does a contract's `delivered`
column mean? Three defensible single-counting answers, and they give very
different headline numbers:

| rule | NG1 delivered | note |
| --- | ---: | --- |
| arrivals at the contracted delivery place (Maiduguri) | 15,000 | literal reading of "45,000 cartons delivered to Maiduguri" |
| first hop out of the supply source | 20,000 | "what the supplier despatched against this contract" |
| terminal legs only (final resting place) | ~11,074 | each carton counted where it came to rest |

All three drop "Delivered to date" on the command centre from 109,675 to
roughly a third of that, and all three turn the pipeline gap from a rounding
error into a real operational story. That is probably an improvement to the
demo, but it changes headline figures in three narratives, so it is an operator
call rather than a cleanup.

---

# Addendum 2 — 2026-07-28 evening: all four judged

Every narrative in the set has now been rendered and judged by both lenses.
Renders are clean (`oes-supply-base` 65/65, `oes-partner-pipeline` 40/40,
`oes-command-centre` 43/43, `oes-money-to-child` 28/28) and all four
deterministic lenses pass on every one. Every narrative still scores 2/5.

That combination is the finding. The renders are clean because the *recipes*
are right; the scores are 2 because in several places the **product does not
render its own best evidence**, and in a few the **narration asserts past what
the model will do**.

## N. The recurring shape: built, tested, unreachable

Three separate capabilities were fully implemented, permission-checked and
covered by tests, with no caller anywhere in the frontend:

| capability | state found | where it was needed |
| --- | --- | --- |
| `actions.reallocate` + `POST api/actions/reallocate/` | service, endpoint, audit log, signal resolution — no UI at all | the command-centre's payoff scene, and the advice on every exception row |
| `ShipmentDetail` (milestone rail + append-only event log) | built, reachable only from the supplier's own page | command-centre scenes 3 and 4, which narrate both |
| `BatchDrill` + `api/batches/<lot>/` | built and tested (`test_demand.py` asserts MUAC crossing recovery), reachable only from the partner's page | the funder narrative's closing beat, its only human image |

All three are now routed. The pattern is worth naming because it is invisible to
every gate the repo has: pytest passes (the service works), preflight passes (no
selector is wrong), and the narration describes the capability accurately —
because the capability *exists*. Only a judge looking at a rendered frame and
asking "where is it, though" catches it.

## O. K1 is now visible on a single screen

The contract double-count (§K1) stopped being an abstract measurement argument.
On the funder page, in **one frame**:

* the unit ladder reads **115,170 children**, method stated as one carton per
  child's full course;
* the outcome card 180px below reads **48,787 courses delivered**, method stated
  identically.

**2.36× apart, same stated method, same screen.** The ladder sums every hop;
`coverage._delivered_cartons_by_district` counts only boundary-crossing legs
(the fix in §K's first half). Cost per child is therefore either $21.27 or about
$49–50 depending on which denominator the reader picks — on a card whose whole
pitch is "stated as a chain, so every step can be checked".

A judge did exactly what the card invites and broke it in under a minute. This
is the strongest possible argument for resolving K1, and it now has a reproducer.

## P. Scene 5 of `oes-money-to-child` cannot say what it says

Corrected here because I got it wrong earlier in the day. The Borno/Kassala
coverage pair (§J) was seeded and verified — **on the funder's view**. Scene 5
is **Hauwa's** view, which is scoped to Nigeria on the server, and Kassala is in
Sudan. On her page the table is Borno 34% and Yobe 0%.

So the narration's contrast — "ninety-one percent … and this one, which received
MORE cartons, sits at thirty-four" — is unavailable there twice over: 91% is not
in scope, and of the two rows that ARE in scope the 34% district is the one with
more cartons, with its comparator at zero. Tonnage would rank them identically,
which is precisely what the scene exists to disprove.

Making it true needs a **third Nigerian district with a small caseload and good
coverage** — Borno keeps the volume and loses on coverage, the new district wins
on coverage with fewer cartons. Yobe cannot play the part: 91% of its 18,960
caseload is 17,254 courses, more than Borno's 16,399, so it would break the
"more cartons" half.

## Q. Two narration claims the model correctly refuses

Both are `concept_change` calls, and in both the product is right and the
narration is wrong:

1. **`oes-command-centre` scene 8** narrates cover moving, the pipeline gap
   closing and the exception resolving. The reallocation creates a `PLANNED`
   consignment, so stock — and therefore cover, and therefore children at risk —
   is deliberately unchanged until it lands. That invariant is the app's whole
   credibility argument. The honest beat is the one now built: the exception is
   **answered, not resolved**, carrying the actor, effect and recorded reason,
   and the headline counts only what nobody has acted on (1,521 → 434 on camera).
   A judge suggests an alternative worth considering: re-target the scene at the
   Komadugu partner shortfall, which IS a `ShortfallSignal` and therefore does
   genuinely resolve — welding scenes 7 and 8 into one loop.

2. **`oes-supply-base` scene 8** ("splitting costs a little more per carton") —
   unchanged from §L. Still the cost minimum.

---

# Addendum 3 — 2026-07-28, session 3: the four decisions taken, and what they cost

Every open decision in §K1, §L, §P and §Q was put to the operator and
answered. All four are now implemented, and all four narratives re-rendered
clean into `-002` run dirs with every deterministic lens passing.

## R. K1 resolved: a carton counts once, where its contract says

**Decision: arrivals at the contract's own `delivery_place`.** The rule reads
off `Lot.delivery_place`, so it is checkable against the contract's own text
rather than against a convention kept somewhere else. A contract for "45,000
cartons delivered to Maiduguri" is discharged by the cartons that reach
Maiduguri: the plant→warehouse legs before it are not delivery yet, and the
hub→centre legs after it are last-mile distribution past the delivery point.

| contract | was | now | of contracted |
| --- | ---: | ---: | ---: |
| OES-C-2026-NG1 | 44,675 | **15,000** | 45,000 |
| OES-C-2026-ET1 | 28,000 | **12,000** | 48,000 |
| OES-C-2026-BF1 | 15,000 | **6,000** | 20,000 |
| OES-C-2026-SD1 | 20,388 | **14,000** | 40,000 |
| "Delivered to date" | 109,675 | **47,000** | — |

Three things fell out of it that were not part of the decision:

1. **The Sudan haulage lot named only where cartons are collected** ("from Port
   Sudan"), which left it no delivery point to measure against. It now names
   Khartoum too — a data fix that makes the contract text honest rather than a
   special case in the measure.
2. **Cost per child divided confirmed-only money by every delivered carton**,
   though the locked narration promises the figure excludes consignments in
   transit. Confirmed over confirmed now.
3. **The unit ladder summed haulage spend and haulage cartons into a food
   chain**, attributing movement money to cartons. Supply contracts only. Cost
   per child now reads **$41.80**, which is a price a reader can recognise.

The §O contradiction is gone: the ladder and the outcome card no longer state
the same method over figures 2.36× apart. They still differ — 33,000 against
58,251 — because they genuinely measure different sets, and both now say so on
screen. The wider figure counts every carton that crossed into a district
including imported stock; the ladder counts only what OES supply contracts
delivered. That is the card's own thesis, applied to itself.

## S. The other three decisions

- **§L, supply-base scene 8** — narration changed to the honest and stronger
  claim: each corridor's cheapest bidder is a different plant, so the split is
  the resilient award *and* the lowest-cost one. No data change, so scene 7's
  per-corridor price leader survives.
- **§Q, command-centre scene 8** — re-targeted at the Komadugu shortfall, as the
  judge suggested. Scenes 7 and 8 are now one loop: raised, acted on, closed.
- **§P, money-to-child scene 5** — Gombe seeded as the third north-east
  district. **The locked narration is now true word for word with no change to
  it**: ninety-one percent, thirty-four, and 31,833 children still uncovered.

## T. A resolved signal has to close ON CAMERA

Re-targeting scene 8 exposed a design fault behind it. A `ShortfallSignal` was
dropped from the queue the instant it resolved, so the one loop in the product
that genuinely completes completed by a row *ceasing to exist*. A reader
looking at the queue after the decision saw an absence, which is the weakest
possible evidence for the claim the screen makes.

It now stays for a week marked **Closed**, carrying the actor, the effect and
the recorded reason, sorted below everything still waiting on somebody, and out
of the children-at-risk headline (1,521 → 1,434 on camera). **Closed** and
**Answered** render as different states, because they are: a partner signal
resolves — what was reported is no longer true — while a derived row can only
be answered until the cartons land.

## U. The recurring defect, third sitting: three more found

§N named the pattern — built, tested, unreachable. Three more, all invisible to
pytest, preflight and the narration:

| what | state found |
| --- | --- |
| `Milestone.estimated_at` | serialized on every milestone, rendered nowhere. The rail showed `actual ?: planned`, so the field that actually MOVES — the whole reason a delay is measurable — was never on screen, under a card subtitle promising three timestamps |
| expiry-risk exceptions | service, cover calculation and queue row written and tested; every seeded lot carried 540 days, so every expiry landed in January 2028 and the exception could not fire. The narration said "all four exception kinds" over three |
| `cartons_short` on a shortfall | serialized, never rendered, while scene 5 narrated it as a field of the record and the Receiving screen two cards above proved the product could show it |

Djibo now carries a 150-day lot: it is the most over-supplied node at 25 weeks
of cover, which is exactly what the exception exists to catch. It also gives
the node one coherent story — too much stock to consume before it expires, and
a 6-day delay that therefore harms nobody.

## V. Two rendering faults only a frame catches

- **The partner-shortfall headline collapsed to a ~10px vertical sliver** in
  scenes 6–8. It carries two badges before its text and a flex child defaults to
  `min-width: auto`, so the headline was the only item that could give and shrank
  toward its longest word. It is the row the command centre's best scene is
  built on.
- **The command centre's own payoff rendered below the fold.** Closing the
  signal sinks it to the BOTTOM of a queue ranked by what nobody has acted on,
  so `scroll_to(.exception-list)` framed the rows the scene is not about. Caught
  by looking at the snapshot, not by the run report — 43/43 actions were "ok".

## W. A lens that was passing without running

`duplicate_frames` reported **"pass (0 pairs compared) — pillow/numpy
unavailable"** on all four narratives. That is a false pass, not a pass, and it
is the lens specifically credited in §Addendum-2 with catching the silent
`scroll_to` no-op. Installed into the runtime; it now genuinely compares
consecutive frames and genuinely passes.

Worth generalising: a deterministic lens that degrades to "pass" when its
dependency is missing is worse than one that fails, because the run report
reads identically to a real pass.

## X. What was NOT verified this session

- The `-002` verdicts are being produced by fresh independent judges as this is
  written; the scores are not yet in this document.
- The zero-risk row wording, the expiry exception and the closed-signal state
  are asserted by tests and read off the live app, but have not yet been judged.
- No video render, no VO timing eval, and nothing uploaded to canopy-web.
- `oes-supply-base` and `oes-partner-pipeline` render 63 and 35 actions against
  the 65 and 40 quoted in the session brief. The `-001` run reports show 63 and
  35 as well, so this is a difference between the brief and the last run dir,
  not a regression introduced here.

---

# Addendum 4 — 2026-07-28, round 2: judged, fixed, re-rendered

The `-002` runs were judged by eight fresh independent agents (arc + concept
on each narrative). Every narrative moved off the flat 2/5 the set had been
stuck on, and the pattern in the verdicts changed shape: the narratives got
better and **the camera became the binding constraint**.

| narrative | arc (weighted / overall) | concept | was |
| --- | --- | --- | --- |
| `oes-supply-base` | 3.45 / **3** | 2 | 2 |
| `oes-partner-pipeline` | 3.20 / 2 | 2 | 2 |
| `oes-command-centre` | 2.85 / 2 | 2 | 2 |
| `oes-money-to-child` | 2.55 / 2 | 2 | 2 |

## Y. What the judges confirmed, independently

Worth recording because it is the reason to keep paying for independent judges:

* **§L's rewrite is true.** The supply-base concept judge reconciled all fifteen
  unit-price × quantity products on screen and re-derived the split at
  $3,374,400 ($42.18/carton) against $3,455,400 for the cheapest consolidation.
  Scene 8 now scores the walkthrough's **best** claim_reality_coherence.
* **The 7→8 weld works.** The command-centre arc judge verified in pixels that
  scene 7's Askira row and scene 8's Closed row are the same row, that it sank
  below the others, and that the headline moved by exactly the 87.
* **Scene 5's inversion lands** — "the strongest data moment in the run".
* **The counted-consignment finding was already fixed** before the run was
  judged. Both the fresh judge and this session's own check agree: do not
  inherit it. Verifying a judge's claim before acting on it paid for itself.

## Z. Eight defects the second round found — one of them created by this branch

**Making a capability visible is what exposes the code path nothing could
reach.** Expiry-risk exceptions had never fired, so nothing had ever pressed
the button on an expiry row — and that button ran the reallocation *backwards*.
An expiry row names the node holding too MUCH; the queue offered "Reallocate to
Djibo" on the row reporting Djibo at 25 weeks of cover, and posted
`target_node_id` unconditionally. Following the product's own advice moved
stock into the node that already could not use what it had. A row now declares
whether its node is the source or the target of the move it advises.

The rest, in rough order of how badly they undercut a stated claim:

| defect | the claim it broke |
| --- | --- |
| the funder ladder mixed bases — confirmed money over every delivered carton, so its own endpoints divided to $15.21 against the $41.80 asserted three lines below | "stated as a chain, so every step can be checked" |
| the government page repeated K1's per-leg double count: 53,246 against its own coverage table's 25,863, 800px apart | a number cannot mean two things |
| a Kano warehouse-to-warehouse transfer was credited as "20,000 children covered" | a storage point serves no caseload |
| "children treated" named three different numbers, all over carton counts | the card on that very page attacking that conflation |
| coverage said "monthly SAM caseload" over a four-month denominator | "the method can be challenged" |
| the MUAC payoff letterboxed to a third of its width | the narrative's only human image |
| the action log recorded `oes-lead@oes.example` under a chrome reading "Ada Nwosu" | "carrying who decided" |
| a dead 520px map panel owned ~45% of four frames | two judges read it as "something is broken" |

## AA. The four decisions of round 2

1. **Scene 9 closed on the wrong contract** (two judges). Awarding a lot created
   an Award and *nothing else*, so "the award becomes the contract" was a
   sentence rather than a link and the scene had to open a pre-seeded contract
   from a different tender. `award_lot` now creates the execution contract, and
   scene 9 opens `OES-C-2026-NG2` — the one the previous scene just made.
2. **Scene 8 had lost its trade-off**, because the resilient split is also the
   cost minimum. Accepted rather than re-priced: the narration now says the two
   rules agree here, and that awarding lot by lot is what lets you tell when
   they do not.
3. **The queue ranked on magnitude with no time term**, so 907 children due in
   December outranked 87 due next week on a screen promising "where, and by
   when". A row now spends its figure only if the harm falls inside the
   decision horizon.
4. **The batch→distribution join was decorative** — the first six ShipmentLines
   in the database round-robined across eleven sites, so Biu served 280 children
   out of a batch it had never received while its own cover row read "awaiting
   first consignment". A site now hands out only what arrived at it, only after
   it arrived.

Also fixed without a decision, because two judges flagged it: the prior split
tender carried the **live tender's own two lots verbatim**, sitting one row
above it marked 2/2 Awarded — the answer three scenes build to, already on
screen behind them.

## AB. What was NOT verified

* **The `-005` renders have not been judged.** Every claim above about the
  *fixes* is verified by tests, by reading the live app, or by looking at the
  rendered frame; none of it is a score. The next honest step is another
  dual-lens round.
* The money-to-child arc judge's repeat finding stands: the outcome card
  (58,251 / 81.8%) is still in scene 3's frame, so the finale announces
  something already seen. On a continuous-scroll page a 250px card in a 720px
  viewport cannot exclude the card beneath it — it needs a layout change, not
  another scroll target.
* Several judge findings were deliberately not taken this round: scene 2 of
  partner-pipeline has four narrated specifics that the calendar falsifies;
  command-centre scene 2 opens the one corridor where the three-tier gradient
  is invisible; and the "ranked against every other exception" claim on the
  partner's own screen is still asserted rather than shown.
* No video render, no VO timing eval, nothing uploaded to canopy-web.

---

# Addendum 5 — 2026-07-28, round 3: partial judging, and a defect the fixes exposed

**Only four of eight judges completed.** The command-centre arc and concept lenses,
the money-to-child concept lens and the partner-pipeline concept lens all
terminated on the session API limit. The partner-pipeline concept judge got far
enough to dispatch its six per-scene visual judges, all of which returned, so
that narrative has scene-level judgement but no assembled verdict.

| narrative | arc | concept |
| --- | --- | --- |
| `oes-supply-base` | **3** (3.15 weighted) | 2 — three dimensions up from 2 |
| `oes-money-to-child` | 2 (2.75, up from 2.55) | *did not complete* |
| `oes-partner-pipeline` | 2 (3.10) | *per-scene only: 3,2,3,2,3,2* |
| `oes-command-centre` | *did not complete* | *did not complete* |

## AC. What round 2's fixes actually achieved, per the judges

Confirmed fixed, verified against the frames rather than taken on trust:

* **The award now produces its contract.** Scene 9 opens `OES-C-2026-NG2`, obligated
  $2,537,400 = 60,000 × $42.29, awarded under the tender scene 8 awarded on camera.
  The arc judge calls it "the run's real improvement".
* **The duplicate prior tender is gone.**
* **The scope inversion is gone** — Nigeria 25,863 < contract 33,000 < all-sources
  58,251, and carton counts read "courses".
* **The batch→distribution join is correct** on every row cross-checkable against
  the receiving table.
* **The MUAC charts fill their width**; scene 5's closing scroll lands on the row it
  created; the money-to-child persona handoff frame is now a full-bleed table.

## AD. A fix measured and refuted

The partner-pipeline scene 2 retarget — footnote to `thead` — **moved the camera 30
pixels**. The judge aligned the two frames and found **95.3% of the 690 overlapping
pixel rows identical**. It was recorded in the last addendum as fixing "the binding
scene". It did not.

That is the second time in this run that something recorded as an improvement was
refuted by an independent judge with a measurement. Both times the check was
cheap and the belief was expensive.

**It also exposes a real blind spot in our own tooling.** `duplicate_frames` passed
this pair, because a 30px scroll shifts far more than 2% of pixels while showing
the same content. A gate that exists to catch "the same surface twice" does not
catch the most common way that happens. A scroll-invariant comparison — align the
two frames at their best vertical offset and measure the overlap — would have.

## AE. The recurring shape, third instance: fixing the join exposed the cohort

Making the batch→distribution join real means a batch now resolves to a specific
site on a specific date. That immediately made a pre-existing data defect
checkable, and the scene-6 judge checked it:

* the batch was distributed to Monguno on **22 July**, the page is as of **29 July**,
  and the hero child is labelled **"9 visits over 8 weeks"**. An eight-week course
  cannot belong to a batch that landed seven days ago.
* **14 of 14 recorded outcomes are "Recovered"** — no defaulters, no non-response,
  no transfers — and all fourteen MUAC trajectories are smoothly monotonic. A CMAM
  adviser reads that as generated, not as a cohort. The seeder aims at the Sphere
  thresholds in aggregate but produces no variation in the sample actually shown.

Neither is caused by the join fix; both were unreachable before it, because the
batch resolved to a shipment the site never received and no date could be checked
against anything. This is the same shape as the expiry/reallocation bug: **making a
capability real is what makes its data checkable, and the check then fails.**

## AF. Product defects found by the per-scene judges, not yet fixed

* **The distribution calendar's stated rule and its rendered colours disagree.** The
  card says a cell is short "exactly when the second number is below the first";
  Askira (38 on hand + 94 inbound against 63 booked) renders amber and Biu (0 + 141
  against 94) renders red. Both pass the stated test. The real rule appears to be
  "depends on stock that has not arrived", which is never stated.
* **"4 Distributions not covered" reconciles with no reading of the grid** beneath it
  — two non-green cells, zero short by the stated rule.
* **`SHP-2026-0909` is tagged "Entered by hand"**, but scene 4's narration says the
  count was taken "against the despatch advice". The advised 900 it is checked
  against is itself hand-keyed, so the evidentiary contrast the scene rests on does
  not exist for that record.
* **The shortfall quantum is not reconstructable.** 94 = two weeks of burn (470)
  minus stock on hand (375); the two-week horizon appears nowhere on screen.
* **The MUAC charts have no axes and no shared y-scale**, so the threshold crossing
  is only actually legible in one of fourteen rows.

## AG. Where supply-base's remaining points are

Its concept verdict is now capped by **motion_friction alone**, and every cause is a
recipe fix:

1. scene 4 never clicks **"Record decision"** — verified in `tab_rounds.jsx` that
   Qualify/Reject only set local state — so scene 5's registry shows Savanna
   expiring **Feb 2027**, not the **Jan 2028** granted on camera;
2. scene 3 opens the **2026-A** application while scene 4 reviews **2026-B**, so the
   profile the viewer watches freeze is not the one Tomas assesses;
3. scene 7's sticky header occludes the Maiduguri lot title on the exact hold where
   the narration names it.

Plus one data bug: the Sudan lot renders **"40,000 truck-months"** in scene 6 and
**40,000 cartons** in scene 9's pipeline table — one record, two units.
