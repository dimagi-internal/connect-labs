# DDD Context — Nutrition Demo (OES/ECF Program Admin Report)

## Project

A funder-facing (OES/ECF) demo of the labs **Program Admin Report** for a child-nutrition
program: a program manager overseeing three RUTF/MUAC **network managers** reads all three
managers' weekly reviews as one grid and drills program → network manager → frontline worker →
individual child's MUAC evidence. Rendered against the live labs dashboard on
`labs.connect.dimagi.com`.

## base_url

https://labs.connect.dimagi.com
Auth: labs browser session at `~/.ace/labs-session.json` (seeded out-of-band via
`bin/ace-labs-walkthrough-login` / `/ace:labs-login`). The spec carries NO `auth` block —
cookies are seeded before the render; `record_video` gets `--storage-state ~/.ace/labs-session.json`.

## The narrative (already authored + validated + locked)

- Spec: `docs/walkthroughs/nutrition-demo.yaml` (UnifiedSpec, `narrative_locked: true`).
- WhyBrief: `docs/walkthroughs/nutrition-demo.why_brief.yaml`.
- Both pass `scripts.ddd.validate` and `scripts.ddd.spec_qa` (2026-07-21).
- Setup: `scripts/walkthroughs/nutrition-demo/ensure_env.py` → `realized.json` (`rerun: once`;
  the env is pinned + already deployed, so the checked-in `realized.json` is authoritative).

The story is approved by the operator (Jon) — this run should skip re-authoring, hydrate the
locked narrative, and go straight to render → dual-judge → route findings → converge → Video
phase → upload.

## The data (live + deployed on labs)

Env `nutrition-demo` (`connect_labs/labs/synthetic/envs/nutrition-demo.yaml`): three opps filed
under **PROGRAM 10110**, so the cross-opp Program Admin Report rollup is **program-owned**
(viewed via `&program_id=10110`; program-owned workflow support = connect-labs #945/#946/#948).

- **Northern** (10010, Amara Nwosu): 10 solid FLWs, no flags → aggregate reads **SOP MET**.
- **Central** (10011, Bakary Diallo): two bad MUAC distributions → **BELOW**. Drill targets:
  `kadi_c` (Kadi Fofana) flagged wk1/May 11, coached-to-close (audit 4996 / task 5000 closed);
  `lola_c` (Lola Kargbo) flagged wk2/May 18, investigation still open (audit 4997 / task 5001).
- **Eastern** (10012, Chidi Eze): two misleading-MUAC-photo FLWs the AI image review flags →
  **BELOW**. Drill target: `vida_c` (Vida Kargbo) suspended for misleading photos (wk2/May 18).

Realized PAR: program-owned def 5003, run 5005, `program_id=10110`
(`/labs/workflow/5003/run/?run_id=5005&program_id=10110`).

## Requirements (evidence)

- Operator brief: OES/ECF nutrition program-management demo; arc program manager → network
  manager → FLW → individual child (MUAC recovery over the review weeks + the audit photos).
- Env + program-owned workflow support shipped: connect-labs PRs #945 #946 #948 #949.
- Modeled on the proven 2-opp walkthrough `docs/walkthroughs/program-admin-report.yaml`.

## Narrative direction

Program manager Priya reads three network managers on one grid, sees which met the SOP and which
fell below, and drills from any cell down to the network manager's own review, the flagged
worker's audited child-MUAC photos, and the AI coaching transcript that resolved (or is still
resolving, or suspended) each case. Closing frame: the SOP MET vs BELOW contrast across the three
managers — the sixty-second call on where her attention goes this week.

## Known gap (honesty)

A dedicated per-child MUAC-trajectory dashboard (red→yellow→green over the weeks) is NOT built;
the child's recovery is shown via the audited MUAC photos + the closed coaching outcome. Tracked
as WhyBrief gap G1 (CAPABILITY, claim_ref S4).

## Current phase

CONVERGED (concept 4.0/5 pass, user-artifact 4.0/5 pass) — run
`nutrition-demo-2026-07-22-003` (2026-07-22), FULL 12-scene spec incl. the NEW
closing **scene 12 "Priya follows one child's recovery"** (SAM Follow-up Timeline,
workflow 5017 / opp 10036). Scenes 1-11 render byte-identical to run 002's 5.0;
scene 12 (the only new scene) drives the sub-5s (visual_polish 4, trust 4,
claim_reality 4) — the per-child MUAC recovery reads as a clean color-coded list
(red 11.0cm → green 13.5cm recovered) rather than a bespoke curve; children shown
as privacy-anon "Beneficiary N" (real source child_name null). Narrative **v3**
posted to canopy-web (review ff96b68a). Internal `/ddd` package published (--stuck):
`https://labs.connect.dimagi.com/canopy/w/connect/ddd/nutrition-demo/nutrition-demo-2026-07-22-003`.
Scene 12 required: (a) broadening opp 10036 allowed_domains to +@dimagi-ai.com via
ECS (deployed labs :506 lacks the dimagi-internal is_accessible_to bypass), (b)
pointing scene 12 at saved run_id=5019 (workflow 5017 executes live via "Create
Run"), (c) fixing the drill to the row's Timeline button. External/public release
+ a narrated-VO hero (run 002-style) remain as gated operator finishing steps.

### Prior phase
CONVERGED at 5.0/5 (both judges) — run `nutrition-demo-2026-07-22-002` (2026-07-22).
Up from the prior run's 4.0. Both prior gate root-causes fixed & verified: scene-3
Actions-column clip (→ `video_viewport_width: 1600`, full table renders) and the
claim_reality badge over-application (→ connect-labs #954: exactly 2 Hyperzoomed
badges live). One mechanical render-recipe fix this run: scene-10 `text:Hyperzoomed`
→ `css:text=/^Hyperzoomed$/` (the substring selector grabbed a hidden `Not
Hyperzoomed` span as `.first` after the data fix). Narrated hero re-rendered fresh
with corrected s10 VO; timing 4.44, video-judge 4.0. Internal `/ddd` package
published (`--stuck`, OAuth-gated) under narrative v2:
`https://labs.connect.dimagi.com/canopy/w/connect/ddd/nutrition-demo/nutrition-demo-2026-07-22-002`.
External stakeholder publish remains a gated operator action (`ddd-upload` WITHOUT
`--stuck`). Run stays iterable.
