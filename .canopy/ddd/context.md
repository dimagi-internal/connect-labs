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

Feature slug: `create-survey-solicitation`. Goal: the **missing middle** between two
shipped demos. The `study-design` demo (`microplans-study-groups`) produces the **R6
"Attakar × Gura" two-arm study plan** on program/opp **10008**; the `verified-monitoring`
demo's **Round 6** consumes that same plan to ground household-survey records (the 68.1%
vs 8.9% hero round). Today the independent survey firm that runs those household surveys
exists only as narrative framing — there is no recruitment step. This narrative is that
step: from the R6 plan, **Maya** (the program owner who designed the study) clicks
**"Create solicitation"** (the now-shipped _create-a-solicitation-from-a-micro-plan_
feature, PR #616, deployed) to recruit a household-survey firm; the plan rides over as a
selectable **coverage area**; the survey firm opens the public solicitation, **selects the
coverage area and applies**; and Maya sees the firm's selected coverage on the response
(one reviewer beat). That firm is exactly who would supply the T1–T6/C1–C5 enumerators
feeding R6.

Scope (tight): create-from-plan → publish → firm applies (selecting the plan) → reviewer
sees selected coverage. Personas: **Maya** (program owner, continuity with study-design)
creates; an independent **survey firm** (respondent persona, e.g. Amina Okafor / Health
Bridge Nigeria) applies. Single auth session (ace CLI token); personas are narrative
framing across scenes, as in `docs/walkthroughs/solicitations.yaml`.

**Honesty / claim_reality_coherence (load-bearing):** create-from-plan, the coverage-area
list, respondent selection, and _capture for review_ are SHIPPED (v1 = capture only). The
downstream **award → provision a Connect opportunity → enumerators actually run R6** is the
SAME deferred capability gap noted above — the narrative must frame that tail as
aspirational end-state, not a shipped mechanism. The award flow is intentionally untouched
by PR #616.

Key live identifiers: program/opp **10008**; R6 plan/group **"R6 — Attakar × Gura"**
(input_areas: Attakar=intervention, Gura=comparison); SD opp 10010; VM workflow def 3699.
Seeders: `scripts/walkthroughs/study-design/ensure_study.py` (study/plan) +
`scripts/walkthroughs/verified-monitoring/demo_config.json` (single source of truth). The
solicitation + a response are seeded via the labs MCP / `SolicitationsDataAccess` against
opp 10008 (labs-only → local records backend, no prod permission checks).

## Key references (memory)

- Microplans generalization shipped (PR #299); program portfolio layer #305–#309;
  unified two-phase architecture #314–#336; service-delivery GPS overlay #324.
- Clustering uses UTM (intentional divergence from connect-gis degrees).
- Existing walkthrough specs: `docs/walkthroughs/microplans-portfolio.yaml`,
  `microplans-service-delivery.yaml` (old walkthrough format, useful as scene source).
