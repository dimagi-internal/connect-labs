# DDD Context — CommCare Connect Labs

## Project

Labs / rapid-prototyping environment for **CommCare Connect**, operating entirely
via API against production Connect. Deployed at `https://labs.connect.dimagi.com`.
Auth for walkthroughs is established out-of-band via `/ace:labs-login` (headless
OAuth-via-CCHQ; cookies imported into the gstack browse profile).

## Active feature — microplans (full create → push-to-opp cycle)

`microplans/` is the most actively-developed app. It lets a **program owner** plan
field-worker microplans _before_ any Connect opportunity is provisioned:

- **Setup** — pick an admin boundary, choose sampling (building-as-WorkArea) or
  coverage (cluster/grid-as-WorkArea), generate a grid of work areas.
- **Two-phase algorithm** — Phase 1 grouping (bbox / BFS-adjacency) + Phase 2
  assignment (manual / round-robin / Neal-Lesh minimax spread). `/regenerate` is a
  destructive rebuild; regroup / reassign tune in place (now Celery-offloaded).
- **Review & tune** — per-FLW metrics table, editable work-area list, exclude /
  regroup / reassign controls; partner validates before upload.
- **Portfolio & lifecycle** — program owns a portfolio of candidate plans, each
  moving Draft → In review → Approved → Deployed via `program_plan_transition`.
- **Compare** — head-to-head composite fit score (spread / balance / coverage).
- **Share** — curated plan-group proposal page for an LLO partner.

Program workspace lives at `/microplans/program/<program_id>/`. Demo program = 133.

### Planning vs execution boundary (load-bearing for the narrative)

Labs owns **creation + planning** (setup, tuning, lifecycle, LLO edit UI, audit).
**Connect owns execution.** The actual **transfer of an approved plan into a live
Connect opportunity is DEFERRED** — there is a `Deployed` lifecycle _status_ but no
mechanism that pushes work areas into an opportunity's WorkArea set. The Connect side
models work areas with pghistory; labs mirrors that shape, phase-tagged.

→ Any narrative whose arc ends in "push to an opp" must treat the final push as a
**CAPABILITY gap** (aspirational end-state), not a shipped feature. Honesty about
this is the whole point of the why-brief / claim_reality_coherence checks.

## This run

Feature slug: `microplan-to-opp`. Goal: a _basic_ end-to-end narrative — one program
owner creating a microplan and carrying it through to being pushed to a Connect
opportunity. Keep it tight; this is the spine demo, not an exhaustive tour.

## Key references (memory)

- Microplans generalization shipped (PR #299); program portfolio layer #305–#309;
  unified two-phase architecture #314–#336; service-delivery GPS overlay #324.
- Clustering uses UTM (intentional divergence from connect-gis degrees).
- Existing walkthrough specs: `docs/walkthroughs/microplans-portfolio.yaml`,
  `microplans-service-delivery.yaml` (old walkthrough format, useful as scene source).
