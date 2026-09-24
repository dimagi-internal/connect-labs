# A demo environment for the OES call — design

**Status:** approved approach, spec awaiting review.
**Date:** 2026-09-24.
**Reads against:** `2026-09-11-rutf-procurement-design.md` (the domain),
`2026-09-23-supply-field-use-cases.md` (what three real procurements needed).

> **Figures live in Drive, not here.** This repository is public. Partner
> *names* and Connect org slugs are named below because they are already
> public and the demo shows them on screen. Per-partner visit counts, worker
> counts, invoice values and supplier prices are **not** in this document, and
> must not be added to it. They live in the Drive-hosted seed data described
> in §5.

## 0. Why

Operation End Starvation launched in June 2026 as a catalytic financing and
delivery platform: $100M from the US State Department over two years, $33M
from founding funders, a stated goal of $4B over six years, chaired by William
Moore of the Eleanor Crook Foundation. It does **not** procure or distribute.
It contracts implementing organisations, and its stated procurement principles
are *cost-effectiveness and transparency* — "working with a diverse range of
producers to ensure we deliver the best value". Phase 1 is scaling RUTF, with
a small basket that includes MMS.

Two consequences for this demo, and they are the whole design:

1. **Their stated principle is our implemented mechanic.** `round_compare`
   refuses to produce a landed cost it cannot defend, and names what to go ask
   instead. That is transparency as a property of the software rather than a
   promise about process.
2. **They contract diverse implementers.** So an implementer who uses the
   supply domain but does *not* use Connect for verified service delivery is
   not an edge case for OES. It is most of their portfolio. It has to be shown
   honestly, including what is lost.

The goal is not a walkthrough. It is a live environment the team can open in
front of OES, click around in, and answer hard questions from.

## 1. What is being built

One org, modelled as if the program team had been running on this domain all
along, plus a second org that uses only half of it.

| Piece | Source of data | Status |
|---|---|---|
| CHC delivery side — four partner opportunities | Synthetic **clone** of program 217 | Build now |
| CHC supply side — the ORS/zinc, vitamin A, dewormer chain | **Seeded** from Drive-hosted data | Build now |
| Partner seats — distributor and collecting LLO | Update links over the seeded chain | Build now |
| Third org — supply, no verified delivery | Seeded, no `opportunity_id` on its points | Build now |
| RUTF — rounds 1 and 2 | **Generated** new | Blocked, see §7 |

## 1a. Three programs, and a portfolio across them

An earlier draft of this document put CHC, RUTF and chlorine into ONE labs
supply program because it was convenient for the seeder. That was wrong, and
the product owner caught it. They are three different things:

| Chain | In Connect | Supply scope |
|---|---|---|
| CHC basket | program 217, org `dimagi-chc-rct` | its own |
| RUTF | program 263, org `dimagi-ng-rutf` | its own |
| Chlorine / safe water | **nothing — it is not an opportunity yet** | its own |

`SupplyDataAccess.scope_key` is "the program, always", and it governs the
catalogue as well as the ledger: commodities, items and suppliers are
per-program, not shared. So one scope holding all three would have put
chlorine in the CHC catalogue and RUTF's supplier register in with ORS — a
program on screen that corresponds to nothing real.

Modelling it correctly also makes the chlorine case stronger. Its supply scope
has **no Connect program behind it at all**, because the delivery program
does not exist yet. A team can track a procurement before there is anything to
deliver with — which is exactly the position the chlorine is in.

### The consequence: there is no view across them

Every surface in the domain stops at one program, by construction. So
"how is this operation doing" cannot be asked at all today — not badly, not at
all. That is the gap this section closes.

### A portfolio is a named set of programs

Deliberately a *set of programs*, not a property of one: a program belongs
to as many portfolios as somebody finds useful, and adding one to a portfolio
grants nothing.

**Access is the load-bearing part.** Supply is program-scoped for a reason,
and a portfolio must never become a way to see a program you could not
otherwise reach. So:

- the view renders only the portfolio's programs that the viewer can already
  reach, from `labs.context.get_org_data(request)["programs"]` — the same
  source the program picker uses;
- when some are hidden it **says so** rather than silently shortening the
  list. A portfolio that shows two of three chains without mentioning the
  third misrepresents the operation, which is the one thing this view exists
  not to do.

**A portfolio is not program-scoped, and every other model in
`supply_chain` is.** That is a real exception and it is stated here so nobody
later "fixes" it by adding a `program_id`. It carries no supply data of its
own — only names and a list of program ids.

### What the master view shows

One row per program, each keeping its own units, because there is no
conversion between a carton of co-pack and a jerry can of chlorine and the
domain already refuses to invent one:

- what is being sourced, what is on order, what has arrived — the stage counts
  `chain_summary` already returns;
- what is **blocked**, including the case where nothing is owed a date (§6a);
- what the record cannot answer, from the checks feed, grouped as the
  Overview groups it.

It does not rank the rows. The Overview's own docstring records that a
priority banner was built and removed because "prioritising is a judgement
about what matters today and the database does not contain what it would take
to make it". A portfolio view has no more information than the Overview does,
so it inherits the same restraint.

### Why this outlives the demo

The same shape answers OES's actual job. Theirs is a portfolio of
*implementers'* programs rather than their own three, and the question —
what is each one running, and what is in trouble — is identical. Building it
for one org is the honest first version of the thing approach B would have
needed.

## 2. The CHC delivery side — clone, do not copy

Program **217, "CHC - NG - RCT - Aug 2026"**, four active opportunities,
each run by a different implementing partner:

| Opp | Partner | Connect org slug |
|---|---|---|
| 2154 | Janna Health Foundation | `janna-health-foundation` |
| 2155 | EHA Clinics (REACH Program) | `eha-clinics-reach` |
| 2156 | Initiative For Social Development In Africa | `isodaf` |
| 2157 | Solina Health | `solina` |

All four end 2026-10-30 and are live now.

**Which of these can be bound to its Connect organisation, and which cannot.**
`org_upsert` takes a `connect_organization_id`, and binding is what "lets that
partner's own staff sign in and record their own shipments, receipts and stock
counts". An earlier draft of this section said all four would be bound. That
was wrong, and the correction is worth keeping rather than quietly fixing.

Connect's `/export/opp_org_program_list/` returns only the organisations the
polling account is a **member of** — sixteen of them. None of the four
partners is among them; `pulse/models.py` documents this directly, which is
why Pulse keys on slug and takes partner *names* from the LLO Directory
instead. The nearest hit is `connect-nigeria` / "Solina ECD Nigeria", which is
Solina's ECD organisation rather than its CHC one: binding to it would be
wrong, not approximate.

So the demo binds what it truly can and leaves the rest unbound:

| Organisation | Bound? |
|---|---|
| The program's own, `dimagi-chc-rct` | Yes — it is in the export |
| The four partners | No — reached through their update links; we record on their behalf until they are bound |

This is the true state of the world, and it makes §5a structural rather than
staged. A bound organisation's staff record for themselves; an unbound one is
recorded *on behalf of*. That is precisely the second-hand-versus-their-own
distinction the demo exists to show, and it now has a cause in the data model
rather than a choice in the seed. If the partners' Connect ids can be
obtained later, adding them is one field per organisation in the Drive
document and no code change at all.

Cloned with the two-phase `profile` → `generate` workflow
(`docs/synthetic-kmc-clone-runbook.md`). Phase 1 builds a **statistical
profile** of each opportunity; phase 2 generates new records from that
profile. Nothing is copied, so no worker identity, GPS trace or beneficiary
record travels into the demo. `synthetic_fidelity_report` is run afterwards
and its output kept, because the first question a funder asks about a demo is
whether the numbers mean anything.

The four differ from each other in ways worth preserving, and the profile
carries them: they have materially different flag rates, different per-visit
economics, and different worker counts. A demo where four partners look
identical is a demo that has been smoothed, and OES's whole job is telling
implementers apart.

**Execution note.** This is a large cohort. The runbook is explicit that
server-side generation drops the MCP transport mid-run at this size. Generate
locally against Drive, then register server-side with
`synthetic_repoint_by_source` / `synthetic_create_labs_only`, so the
generating machine never needs DB access.

## 3. The CHC supply side

The chain from the gap analysis, as it actually ran: the program buys from
EHA; EHA pays the manufacturers, receives and inspects, holds the goods in its
warehouse, and releases them to the implementing partners, who collect. EHA
keeps a live stock-movement sheet and the program reorders off it.

That maps onto the four opportunities exactly, and the mapping is the point:
**EHA is both the distributor and one of the four implementers.** One org,
two roles, which is what makes the partner seat in §4 worth showing.

Commodities: ORS/zinc co-packs, vitamin A, dewormer. Modelled as kits where
the co-pack is a kit (G1), with the real pack configurations.

## 4. The partner seats

Two update links, both login-free, both scoped:

- **EHA as distributor** — confirms orders, records dispatch, records what it
  released to each collecting partner.
- **One collecting partner** — records what it received.

This is the beat that does the most work on the call. OES's implementers'
suppliers will never have logins, and never should. Nothing to roll out is a
feature, not an omission.

## 5. Where the data lives

The seeder **code** stays in this repository — reviewable, testable, diffable.
The seed **data** — partner names as displayed, volumes, pack configurations,
supplier prices, invoice values — moves to a Drive-hosted JSON read through
the existing `connect_labs/labs/synthetic/gdrive.py` client
(`LABS_SYNTHETIC_GDRIVE_SA_KEY`, service account, shared-drive aware).

This resolves a genuine confusion worth writing down, because it has already
cost one design conversation:

| Mechanism | Holds | Where |
|---|---|---|
| `labs/synthetic/` + `fixture_store.py` | Opportunity **visit** data: `user_visits.json`, `completed_works.json`, `payment.json`, `invoice.json`, `app_structure.json` | Google Drive |
| `scripts/walkthroughs/<demo>/seed.py` | All **supply** data: orgs, commodities, quotes, awards, orders, stock | Committed, public |

The two are unrelated despite both being called "synthetic". The existing
supply seeders register their programs with `"gdrive_folder_id": "none"`
precisely because supply data does not come from Drive. The new loader closes
that gap for the supply side without touching the fixture store.

## 5a. Two kinds of truth, and they are already modelled

There are two quite different things in this demo that look alike: what the
program team believes about a supply chain and types in, and what the
organisation running it entered itself. They must not read the same, and in
this domain they already do not.

Every record below the contract carries two independent facts:
`recorded_by_org` (who typed it) and `source` (how they knew it), drawn from
`records.SOURCES`. `Provenanced.witnessed` is true only for `we_recorded` and
`document` — "a missing source is weaker than a partner's claim, not
stronger" — and `identity.source_for()` refuses to let a non-program caller
claim first-hand knowledge at all.

The case worth showing is the one where *we* write down what *they* told us.
`told_by_for` already renders it: **"Dimagi, for EHA Clinics (they told us)"**,
with `reported, not witnessed` beneath.

The demo therefore seeds three tiers deliberately:

| Tier | `recorded_by_org` | `source` | Reads as |
|---|---|---|---|
| Ours, second-hand — the spreadsheet world | Dimagi | `partner_reported` | "Dimagi, for EHA Clinics (they told us)" · reported, not witnessed |
| Ours, first-hand — what we did ourselves | Dimagi | `we_recorded` | "Dimagi" · witnessed |
| Theirs — entered through their own link | the partner | `partner_reported` / `supplier_reported` | "EHA Clinics" |

Seeded so that **one order carries all three**, with the second-hand rows
above the point at which EHA was given its link and its own rows below.
That single screen answers the question an OES program officer actually
has — *what do we really know about our implementers' stock, and how do we
know it* — and it answers it without flattering the data. Onboarding a
partner visibly upgrades the evidentiary status of the record; nothing else
in the demo argues the value of the partner seat as economically.

This is also the most direct expression of OES's stated transparency
principle available in the product, and it costs nothing to build: it is
seed-data choice, not code.

## 6a. Chlorine: the chain that is blocked, with no date

The third thing an implementer's supply chain does is fail to arrive, and a
demo that only shows goods flowing is not describing anyone's real week.

Evidence Action donates the chlorine in kind and imports it. The import was
due in December and is behind, and **nobody knows when it will land**. So the
order carries a quantity, a donor and no promised lead time — which is the
honest state, not missing data.

Most of this is already built and correct. `consideration` has `in_kind`
beside `priced` and `bundled`, so a donation is a first-class order. The stock
page already shows what is owed but not arrived, and refuses to count it as
cover; `_expected_inbound` argues the case in its own docstring: *"a
consignment ninety days late has proved it is not cover. But a store whose
donor still owes it 400 jerry cans is not in the same position as one nobody
owes anything, and the page said the second about both."*

**What is not built is the case where the date itself is unknown.**
`expected_on` is `signed_on + promised_lead_time_days`, and the template
renders the date behind `{% if expected.expected_on %}`. With no lead time
ever promised the sentence simply stops:

> expected: 400 jerry cans from Evidence Action · not counted as cover

It never says that nobody knows when. There is no test covering that branch.
Silence there reads as "fine", when the truth is "we are blocked and we
cannot tell you until when" — the single most important fact in the chlorine
story, and the one a funder asks first.

The fix is small: when `expected_on` is absent, say so plainly rather than
trailing off after the supplier's name. It earns its place in the sequence
because an implementer who reports "blocked, date unknown" is being more
useful than one who reports nothing, and the product should make that
distinction visible rather than flatten it.

## 6. The third org

An implementer that uses the supply domain and runs its own last mile.

Concretely: its supply points carry no `opportunity_id`, and it has no
`kind="user_held"` points. `summary._deliver()` keys off exactly those two
things, so its chain genuinely stops at the last store — the demo is not
staged, it is the same code reading a different world.

What it still gets: comparable quotes, a defensible landed cost, an approval
gate, supplier confirmations, stock on hand, reorder points. What it does not
get: any claim that a commodity reached a beneficiary. Said plainly on the
call, because saying it is more credible than not being asked.

## 7. RUTF — blocked, and how it slots in

**Resolved 2026-09-24.** Organisation `dimagi-ng-rutf` (377), program **263**
"RUTF - NG - Program 1 - Sept 26", opportunity **2230** "RUTF - NG - CBI - P1 -
Sept 26" — 849 visits, ending 2027-02-28. It was absent from Pulse because
Pulse lists only programs carrying visits, and from Connect's org export
because that returns only organisations the polling account belongs to.

Because it just started, there is nothing to clone, so it is **generated**
rather than profiled: a small opportunity built from a manifest, sized to the
real one.

Its supply side does not depend on that access and can be built now: rounds 1
(500 cartons, ordered) and 2 (2,000 cartons, three quotes that cannot be
compared) are described in the RUTF procurement design and are the opening
beat of the sequence in §8.

**Slot-in contract:** the RUTF delivery data is one additional labs-only
opportunity registered under the same demo program. No other piece of this
design depends on it, so it can land after the rest without rework.

## 8. The sequence on the call

The spine escalates: *can you trust the number → can your partners work it →
does it survive without Connect.*

| # | Beat | Screen | The point for OES |
|---|---|---|---|
| 0 | **The whole operation** | The portfolio: three chains, one screen | §1a. What this team runs and what is in trouble, before any drill-down |
| 1 | The problem in their language | RUTF round 2 comparison | Three suppliers, three units, no comparable total — their "best value" problem, unsolved |
| 2 | Refusing to fake it | Same page, unconfirmed cells | It will not compute a landed cost it cannot defend; it names what to ask |
| 3 | Cost per child treated | Comparison, per-course column | Their cost-effectiveness metric, derived rather than typed |
| 4 | The decision, frozen | Award with figures and spec verdict | Who decided, why, at what price, against which specification |
| 5 | The gate | Award, Place order disabled | No purchase until the technical partner confirms |
| 6 | Hand to the partner | EHA's login-free link | Their implementers' suppliers have no logins and never will |
| 7 | It arrives | Receipt: batch, expiry, landed cost | Shelf life against program timeline |
| 8 | The second basket | CHC: ORS/zinc, vitamin A, dewormer | Same machinery, different commodity |
| 8a | **How do we know?** | One order's rows: ours second-hand, ours first-hand, theirs | What we actually know about an implementer's stock, and how — see §5a. Onboarding the partner upgrades the record in front of them |
| 8b | **Blocked, and no date** | Safe-water store: chlorine owed, not counted as cover, arrival unknown | §6a. A funder's first question is what you are waiting on and when it lands. Sometimes the honest answer is "we do not know", and the product says so rather than going quiet |
| 9 | The third org | Supply-only network | Chain ends at the last store — honest about the loss |
| 10 | The contrast | Our org: user-held points, consumption | With Connect the ledger runs to the worker and reconciles against verified visits |

Beats 9 and 10 are the close and are real product behaviour, not staging.

## 9. What is new build

Most of this is assembly. Genuinely new:

1. **A Drive-backed seed-data loader** for the supply side (§5). Small — a
   reader over the existing Drive client, plus a `--data` flag on the seeder.
2. **A CHC supply seeder** over the four-partner chain.
3. **A third-org seeder** — a thin variant of the above with no opportunity
   binding.
4. **The RUTF delivery generator** (§7), once access exists.

Not new, and deliberately not rebuilt: the clone pipeline, update links,
the comparison engine, the approval gate, the stock ledger.

## 10. Risks

- **Clone size.** 200k+ visits across four opportunities. Mitigated by local
  generation plus server-side repoint (§2). Budget a long first run.
- **Fidelity.** A clone that smooths the four partners into one shape destroys
  the demo's best moment. Gate on `synthetic_fidelity_report` before the call,
  not after.
- **RUTF access.** §7 is written so its absence costs nothing but a beat.
- **Naming.** Real partner names are on screen by explicit decision. The
  repository must not gain their figures; §5 is how that is held.

## 11. Open question, not resolved here

`connect_labs/supply/README.md` describes Operation End Starvation as "an
invented multi-country famine-response initiative". OES is a real
organisation and a live prospect, and that sentence is in a public
repository. It should be corrected or the framing reconsidered, separately
from this work.
