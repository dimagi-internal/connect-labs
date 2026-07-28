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
