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

One org, modelled as if the programme team had been running on this domain all
along, plus a second org that uses only half of it.

| Piece | Source of data | Status |
|---|---|---|
| CHC delivery side — four partner opportunities | Synthetic **clone** of programme 217 | Build now |
| CHC supply side — the ORS/zinc, vitamin A, dewormer chain | **Seeded** from Drive-hosted data | Build now |
| Partner seats — distributor and collecting LLO | Update links over the seeded chain | Build now |
| Third org — supply, no verified delivery | Seeded, no `opportunity_id` on its points | Build now |
| RUTF — rounds 1 and 2 | **Generated** new | Blocked, see §7 |

## 2. The CHC delivery side — clone, do not copy

Programme **217, "CHC - NG - RCT - Aug 2026"**, four active opportunities,
each run by a different implementing partner:

| Opp | Partner | Connect org slug |
|---|---|---|
| 2154 | Janna Health Foundation | `janna-health-foundation` |
| 2155 | EHA Clinics (REACH Program) | `eha-clinics-reach` |
| 2156 | Initiative For Social Development In Africa | `isodaf` |
| 2157 | Solina Health | `solina` |

All four end 2026-10-30 and are live now.

The supply-domain organisations are **the same organisations**, not lookalikes:
each is upserted with its `connect_organization_id` set to the real Connect
org above, so the distributor confirming a dispatch and the partner delivering
the visits are one record, and `identity.py` attributes what they record to
them rather than to us. This is what makes the partner seat in §4 mean
anything — it is EHA's own organisation, reached through EHA's own link.

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

The chain from the gap analysis, as it actually ran: the programme buys from
EHA; EHA pays the manufacturers, receives and inspects, holds the goods in its
warehouse, and releases them to the implementing partners, who collect. EHA
keeps a live stock-movement sheet and the programme reorders off it.

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
supply seeders register their programmes with `"gdrive_folder_id": "none"`
precisely because supply data does not come from Drive. The new loader closes
that gap for the supply side without touching the fixture store.

## 5a. Two kinds of truth, and they are already modelled

There are two quite different things in this demo that look alike: what the
programme team believes about a supply chain and types in, and what the
organisation running it entered itself. They must not read the same, and in
this domain they already do not.

Every record below the contract carries two independent facts:
`recorded_by_org` (who typed it) and `source` (how they knew it), drawn from
`records.SOURCES`. `Provenanced.witnessed` is true only for `we_recorded` and
`document` — "a missing source is weaker than a partner's claim, not
stronger" — and `identity.source_for()` refuses to let a non-programme caller
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
That single screen answers the question an OES programme officer actually
has — *what do we really know about our implementers' stock, and how do we
know it* — and it answers it without flattering the data. Onboarding a
partner visibly upgrades the evidentiary status of the record; nothing else
in the demo argues the value of the partner seat as economically.

This is also the most direct expression of OES's stated transparency
principle available in the product, and it costs nothing to build: it is
seed-data choice, not code.

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

The live RUTF opportunity is not reachable from this account yet. It is absent
from Pulse (which lists only programmes carrying visits — consistent with one
that just started), absent from this repository, and the `Connect-RUTF`
programme visible from labs is a tracker-import, not the live engagement.

Because it just started, there is nothing to clone, so it is **generated**
rather than profiled: a small opportunity built from a manifest, sized to the
real one.

Its supply side does not depend on that access and can be built now: rounds 1
(500 cartons, ordered) and 2 (2,000 cartons, three quotes that cannot be
compared) are described in the RUTF procurement design and are the opening
beat of the sequence in §8.

**Slot-in contract:** the RUTF delivery data is one additional labs-only
opportunity registered under the same demo programme. No other piece of this
design depends on it, so it can land after the rest without rework.

## 8. The sequence on the call

The spine escalates: *can you trust the number → can your partners work it →
does it survive without Connect.*

| # | Beat | Screen | The point for OES |
|---|---|---|---|
| 1 | The problem in their language | RUTF round 2 comparison | Three suppliers, three units, no comparable total — their "best value" problem, unsolved |
| 2 | Refusing to fake it | Same page, unconfirmed cells | It will not compute a landed cost it cannot defend; it names what to ask |
| 3 | Cost per child treated | Comparison, per-course column | Their cost-effectiveness metric, derived rather than typed |
| 4 | The decision, frozen | Award with figures and spec verdict | Who decided, why, at what price, against which specification |
| 5 | The gate | Award, Place order disabled | No purchase until the technical partner confirms |
| 6 | Hand to the partner | EHA's login-free link | Their implementers' suppliers have no logins and never will |
| 7 | It arrives | Receipt: batch, expiry, landed cost | Shelf life against programme timeline |
| 8 | The second basket | CHC: ORS/zinc, vitamin A, dewormer | Same machinery, different commodity |
| 8a | **How do we know?** | One order's rows: ours second-hand, ours first-hand, theirs | What we actually know about an implementer's stock, and how — see §5a. Onboarding the partner upgrades the record in front of them |
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
