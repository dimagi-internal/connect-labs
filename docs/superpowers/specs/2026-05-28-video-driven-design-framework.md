# Video-Driven Design (VDD): a repeatable concept-to-video product loop

**Date:** 2026-05-28
**Status:** Design — pending review
**Evolves:** [`2026-03-23-demo-driven-development-design.md`](2026-03-23-demo-driven-development-design.md)
**First exercise:** Rooftop Surveys (this repo) — see [`2026-05-27-rooftop-surveys-app-design.md`](2026-05-27-rooftop-surveys-app-design.md)

## Problem / the insight

Building the rooftop-surveys video walkthrough did something a design doc couldn't: **watching the video surfaced product-design problems that were invisible on paper.** Articulating the concept (narration) while showing the real product (footage) exposed the gaps between them.

Today nothing turns that into a repeatable instrument. Demo-driven development (the predecessor) and the canopy walkthrough loop both optimize *"is the video good?"*. The walkthrough `improve` agent does route fixes to product code, but it chases *slide impressiveness*, not *conceptual soundness*. No tool grades **"did watching this reveal a design flaw / is the concept sound?"** as a first-class output.

VDD is the framework that makes video a design instrument with its own QA/eval self-improvement loop, and ends with a polished explainer you can ship to others.

## Core model

**One artifact, evolving the whole way: a narrated walkthrough where the narration *is* the concept and the footage *is* the product.** Not a concept video then a walkthrough video — one thing, re-rendered against whatever the product currently is. (Per the discovery insight: concept and walkthrough are the same thing during in-depth product development.)

The design signal is the **gap between narration claims and what the footage shows**:
- Claim the footage can't back → product gap (build it) *or* a concept that didn't survive contact with reality (rethink it).
- Footage shows friction the narration glosses over → the awkwardness you only catch in motion.

Every run produces **two independently-scored verdicts**:
1. **Concept verdict** — clarity, design soundness, claim↔reality coherence, motion-surfaced friction. Routes to *product/design fixes*.
2. **Video verdict** — is it a good, shippable explainer (reuses the Tough Judge dims). Routes to *spec/render fixes*.

At convergence the same spec is promoted to the polished shareable video.

## Locked decisions

| Decision | Choice |
|---|---|
| Entry stage / kind of video | Concept and walkthrough are **one evolving artifact** through the discovery loop |
| Eval target | **Both, separately scored** — concept verdict + video verdict per run |
| Autonomy | **Autonomous with pause points** — overnight cycle, hard gates, email digest |
| Home | **New cross-cutting framework identity, built on canopy** ("1 done in 2"); first run in connect-labs |
| Claim↔reality gate | **Scored + surfaced, non-blocking** — findings route to fixers; human decides at pause point |
| Iterating renderer | **Screenshots while iterating, motion only at convergence** |

## The loop

```
scout product state + last run's learnings
        ▼
author / update the UNIFIED SPEC
  (per scene: action + narration/concept_claim + design_intent)
        ▼
QA gate (binary, no LLM)  ──┬──►  seed synthetic data (reuse labs chain)
        ▼                   │
RENDER walkthrough vs LIVE product  ◄┘
  (canopy walkthrough engine → screenshots)
        ▼
CONCEPT JUDGE        ║ (parallel) ║        VIDEO JUDGE
design soundness     ║            ║        Tough Judge dims
claim↔reality        ║            ║        → quality score
→ design_findings[]  ║            ║
        ▼                                          ▼
route PRODUCT fixes (TDD)                 route RENDER/SPEC fixes
        └───────────────┬──────────────────────────┘
                        ▼
        converged? (both ≥ threshold OR max iters)
         no → re-render          PAUSE POINT → human / email digest
                                       ▼ yes
                 PROMOTE → polished narrated video + share link
```

## Component map — reuse vs build

### Reuse (do not rebuild)

| Stage | Existing tool |
|---|---|
| Iterating renderer (drive live app, screenshot scenes) | canopy `walkthrough` skill + `browse` |
| Seed product state for the demo | connect-labs synthetic chain (`synthetic-narrative-plan → data-generate → workflow-seed`) + freshness guard |
| Video-quality verdict | `visual-judge` (Tough Judge) + walkthrough/video-spec eval dims |
| Route product fixes from scene failures | canopy walkthrough `improve` agent routing (`/review`, `/design-review`, `/qa`) — extend its triggers |
| Polished final video | connect-labs Playwright recorder rig (narrated MP4); ACE Remotion as the glossy option (deferred) |
| Verdict shape, QA-gates-eval, run state, pause points | ACE verdict schema + `run_state.yaml` + Pause Points convention |
| Autonomy, digest email, cross-run learnings | canopy PM autonomous loop (`scout→propose→implement→learn`, `.canopy/` persistence) |
| Share/host the artifact | `walkthrough-share` → canopy-web |

### Build new (the real gaps)

1. **Unified spec** — extend the canopy walkthrough YAML with two per-scene fields: `concept_claim` (what the scene asserts the product does and why it matters) and `design_intent` (the design decision under test). This one file is simultaneously the design doc and the video script. *New schema + small authoring skill.*

2. **Concept/design judge** — the centerpiece. Watches the rendered walkthrough and scores **concept clarity, design soundness, claim↔reality coherence, motion-surfaced friction**, and emits structured `design_findings[]`, each tagged `→ PRODUCT` (build/fix) / `→ CONCEPT` (rethink idea or narration) / `→ DEFER`. Claim↔reality is scored and surfaced but **non-blocking**. *New eval skill + rubric (provisional, calibrated after 3 real runs).*

3. **Dual-verdict orchestrator agent** — runs the cycle, fans the two judges in parallel, routes each finding to the right fixer, manages pause points, writes the digest email. Built as a canopy agent reusing `improve`-mode routing + PM-loop autonomy.

4. **Unified-spec QA gate** — binary structural checks (every scene has a falsifiable `concept_claim`, personas resolve, etc.) that gate the judges. Mirrors ACE `-qa`.

5. **Promotion adapter** — at convergence, transform the converged unified spec into a render spec for the polished video (narrated recording for live features). Mostly glue.

### Deferred (YAGNI for v1)

- Concept-judge **calibration fixtures** (defect-creator analog) — ship the rubric provisionally, calibrate after 3 real runs (ACE precedent).
- **Remotion glossy** render path — the labs narrated recorder is enough for rooftop surveys.
- Standalone-plugin scaffolding — v1 lives on canopy.

## The two verdicts (rubric sketch)

**Concept verdict** (LLM judge, weighted dims, own threshold):
- Concept clarity — would a smart outsider get what this is and why it matters?
- Design soundness — does the idea hold together; are the interactions coherent?
- Claim↔reality coherence — does the footage actually demonstrate the narration's claims? (scored, non-blocking)
- Motion-surfaced friction — what's clunky/slow/confusing/backwards when seen moving?
- Output: `design_findings[]` with `route` tag + severity.

**Video verdict** — reuse Tough Judge / video-spec eval dims unchanged.

Both follow the ACE verdict schema (`overall_score`, `dimensions{}`, `verdict`, `auto_surfaced[]`, `gate{}`). QA failure skips both judges.

## Routing

- Concept `→ PRODUCT` findings → TDD product fixes (existing `improve` specialists: `/design-review`, `/review`, `/qa`).
- Concept `→ CONCEPT` findings → edit the unified spec's narration/`design_intent`, possibly re-open the design.
- Video findings → spec/scene/pacing fixes.
- Re-render only what changed; converge when both verdicts ≥ threshold OR max iterations, else **pause** and digest.

## Autonomy & pause points

Autonomous overnight per the PM loop, halting at hard gates: (a) concept not yet approved, (b) a `→ PRODUCT`/`→ CONCEPT` finding above severity needs a human call, (c) converged and ready to promote/ship. Each pause emits an email digest (both verdicts, top findings, what it changed, what it's asking). Cross-run learnings persist so resolved findings aren't re-raised.

## First run: Rooftop Surveys

Exercise the *new* parts against a feature under active design, where the synthetic chain and recorder rig already exist. Success = the loop surfaces at least one real rooftop-surveys design finding we then fix, and emits a shippable explainer at convergence.

## Open questions

- Concept-verdict threshold + max-iteration count (start: ≥4/5 both, 3 iters — match walkthrough `improve`).
- Exact home/namespace for the new skill family on canopy.
- Whether the unified spec replaces or wraps the existing `docs/walkthroughs/*.yaml`.
