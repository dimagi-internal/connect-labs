# Demo-Driven Development v2: a repeatable concept-to-video product loop

**Date:** 2026-05-28
**Status:** Design — approved in conversation
**Evolves:** [`2026-03-23-demo-driven-development-design.md`](2026-03-23-demo-driven-development-design.md) — this is v2 of demo-driven development (same name, expanded into a self-improving loop)
**First exercise:** Rooftop Surveys (this repo) — see [`2026-05-27-rooftop-surveys-app-design.md`](2026-05-27-rooftop-surveys-app-design.md)

## Problem / the insight

Building the rooftop-surveys video walkthrough did something a design doc couldn't: **watching the video surfaced product-design problems that were invisible on paper.** Articulating the concept (narration) while showing the real product (footage) exposed the gaps between them.

Today nothing turns that into a repeatable instrument. Demo-driven development (the predecessor) and the canopy walkthrough loop both optimize *"is the video good?"*. The walkthrough `improve` agent does route fixes to product code, but it chases *slide impressiveness*, not *conceptual soundness*. No tool grades **"did watching this reveal a design flaw / is the concept sound?"** as a first-class output.

Demo-driven development v2 makes video a design instrument with its own QA/eval self-improvement loop, and ends with a documentation page (hero video on top) that explains the capabilities to a prospective user of the feature set.

## Core model

**One artifact, evolving the whole way: a narrated walkthrough where the narration *is* the concept and the footage *is* the product.** Not a concept video then a walkthrough video — one thing, re-rendered against whatever the product currently is. (Per the discovery insight: concept and walkthrough are the same thing during in-depth product development.)

The design signal is the **gap between narration claims and what the footage shows**:
- Claim the footage can't back → product gap (build it) *or* a concept that didn't survive contact with reality (rethink it).
- Footage shows friction the narration glosses over → the awkwardness you only catch in motion.

Every run produces **two independently-scored verdicts**:
1. **Concept verdict** — clarity, design soundness, why-groundedness, claim↔reality coherence, motion-surfaced friction. Routes to *product/design fixes*.
2. **User-artifact verdict** — judges the **docs page + hero video together** from the prospective-user lens: would someone about to use this feature understand the capabilities, the why, and how to use them? (Reuses the Tough Judge dims, audience = feature user.) Routes to *spec / render / page fixes*.

At convergence the same grounded source is promoted to the docs page + hero video.

**Target artifact & audience.** The end product is the **feature's documentation page with a hero video at the top**, aimed at *someone who is about to use the feature set* — it explains the capabilities (and the why behind them) to a prospective user, not a stakeholder sizzle reel. This fixes the tone (instructional/onboarding), the content (capabilities + why + how), and the release target (publish the docs page with the video on top). Every upstream choice — what the why-brief grounds, what the narration says, what the concept judge optimizes — serves that reader.

**The documentation page is itself a build output**, co-authored and co-evolved with the feature — not just a host for the video. This is a DRY win: a *single grounded source* (the why-brief + unified spec) produces three coherent things — the **narration**, the **video**, and the **page prose** — so they can never drift from each other or from the product. The page and video are evaluated together as one user-onboarding artifact (see The two verdicts). The page co-evolves every run; releasing it (publishing for users) is the external-release gate.

## Phase 0: Develop the *why* (concept grounding + research)

**The script does not start by describing what exists — it starts with the why.** And authoring an *honest* why is real discovery work: it routinely reveals that the why isn't yet supported — either we lack the **evidence/rationale** for a design decision, or we lack the **product capability** the why claims. (Rooftop sampling makes this concrete: before we can narrate "we select regions this way with these settings," we have to actually establish *how* the region is selected, *what* the settings are, and *why those settings* — and that justification is a substantial research effort against what we've already built/documented plus new investigation.)

So the loop has a front phase that grounds the why before any walkthrough is rendered:

1. **Audit what exists** — ingest prior artifacts into an evidence inventory: design docs (e.g. `2026-05-27-rooftop-surveys-app-design.md`), the labs sampling code, external research (the rooftop R pipeline + decoded Stage A params in Drive), related Connect microplanning, and relevant memories. Tag each claim-supporting item as *documented*, *implemented*, or *assumed*.
2. **Draft the why-spine** — the narrative backbone: problem → approach → each key design decision → its rationale. Every rationale links to its evidence or is flagged unsupported.
3. **Find why-gaps** — each unsupported claim is tagged:
   - `RESEARCH` — needs investigation/analysis/documentation (e.g. "why 30 buildings per cluster? — decode Stage A params, justify against the R pipeline").
   - `CAPABILITY` — needs the product to actually *do* what the why claims (e.g. "the why says regions auto-select by population density; the app doesn't yet"). **This is the "motivate the why with additional product capabilities" case** — it feeds the build loop.
   - `DECISION` — needs a human taste/direction call (e.g. "cluster-based vs grid-based sampling?"). Routes to the user at the concept gate (blocking).
4. **Close the gaps** — autonomous research/audit drafts candidate answers; `CAPABILITY` gaps become product-build work; `DECISION` gaps go to the user.
5. **Stress-test the why** — a rubric (adapting ACE's `idea-to-pdd` 5-question stress test, with `-qa` + `-eval`) grades whether the why is sound, complete, and evidence-backed. Iterate until it passes.

Output: a **grounded why-brief** that becomes the spine of the unified spec's narration. Each scene's `concept_claim` then carries a **provenance chain** — claim → why-rationale → evidence or capability — which is exactly what the concept judge later checks end to end (claim↔reality coherence becomes traceable, not vibes). The why-brief is re-developed (not rebuilt) whenever the concept changes.

## Review model — what needs you, and how you get it

*This is the load-bearing part. The framework exists to do great work autonomously and pull the primary user in only where their judgment is irreplaceable.*

**Principle: review fires on taste, never on correctness.** For anything correctness-shaped (renders cleanly, well-paced, fixes pass tests, spec is valid) the **eval loop is the proxy** — it decides and reports in a digest that can be read but never blocks. The human is pulled in only where an LLM judge is no substitute. (Rationale from the user: review today is skipped because it fires when they're either *trusting* — "you're close enough" — or *indifferent* — "no opinion." So fire only on irreplaceable taste.)

**The only two blocking gates:**
1. **Concept definition / change** — the direction fork: "is this the right product, framed the right way?" Concentrated up front; a later `design_finding` re-summons the user *only if it implies a concept change*, not an execution gap. (This is why PRs can be skipped: the fork was decided upstream.)
2. **External release** — a video going to other humans. Glance + go.

Everything else runs autonomously. **Framing/voice** is editable but non-blocking — the user rewrites a narration line in place rather than approving it.

**Dual-channel surface — same decisions both ways:**
- **Inline** (`AskUserQuestion`) when the user is live at the keyboard.
- **Async**: overnight run → email digest ("N things need you") → **one editable web review page**: the current cut plays; ≤3 forked decisions with the system's pick pre-selected (accept / redirect); narration inline-editable; a collapsed "what I did autonomously" audit; one **Approve & continue** that resumes the loop.
- Both render from a shared `review_request` artifact per pause, so terminal and web never diverge.

**The flywheel (review shrinks over time):**
- Edits/redirects calibrate the concept judge and teach the narration author the user's *voice* → next draft lands closer.
- **Suggest-then-confirm self-tuning**: track accept-vs-redirect per decision *class*; when a class is rubber-stamped, *propose* downgrading it to digest-only ("you've accepted this 5× — stop asking?") and let the user approve the tuning. Never auto-applied. (Mirrors gstack `plan-tune`.) Net: fewer asks each run, on the user's terms.

## Locked decisions

| Decision | Choice |
|---|---|
| Entry stage / kind of video | Concept and walkthrough are **one evolving artifact** through the discovery loop |
| Eval target | **Both, separately scored** — concept verdict + user-artifact (page+video) verdict per run |
| Autonomy | **Autonomous with pause points** — overnight cycle, hard gates, email digest |
| Home | **New cross-cutting framework identity, built on canopy** ("1 done in 2"); first run in connect-labs |
| Claim↔reality gate | **Scored + surfaced, non-blocking** — findings route to fixers; human decides at pause point |
| Iterating renderer | **Screenshots while iterating, motion only at convergence** |
| Blocking review gates | **Only concept definition/change + external release** block; everything else is autonomous + digest |
| Review surface | **Dual channel** — inline `AskUserQuestion` when live; email digest → editable web review page when async (same decision set) |
| Self-tuning asks | **Suggest-then-confirm** — proposes downgrading rubber-stamped decision classes; user approves the tuning |
| Front phase | **Phase 0 grounds the *why*** (evidence audit → why-spine → gap-find → stress-test) before any walkthrough; gaps split into research / capability / decision |
| Target artifact | **Docs page + hero video for the prospective feature user** — the page is itself a co-evolved build output; one grounded source drives narration, video, and page prose |

## The loop

```
scout product state + last run's learnings
        ▼
PHASE 0 — develop/refresh the grounded WHY-BRIEF
  audit existing evidence → draft why-spine → find why-gaps
  (RESEARCH / CAPABILITY / DECISION) → close → stress-test
        │  CAPABILITY gaps ─► build loop   DECISION gaps ─► concept gate
        ▼
author / update the UNIFIED SPEC
  (per scene: action + narration/concept_claim[+provenance] + design_intent)
        ▼
QA gate (binary, no LLM)  ──┬──►  seed synthetic data (reuse labs chain)
        ▼                   │
RENDER walkthrough vs LIVE product  ◄┘
  (canopy walkthrough engine → screenshots)
        ▼
CONCEPT JUDGE        ║ (parallel) ║   USER-ARTIFACT JUDGE
design soundness     ║            ║   page+video, user lens
why-grounded         ║            ║   Tough Judge dims
claim↔reality        ║            ║        → quality score
→ design_findings[]  ║            ║
        ▼                                          ▼
route PRODUCT fixes (TDD)            route RENDER/SPEC/PAGE fixes
        └───────────────┬──────────────────────────┘
                        ▼
        converged? (both ≥ threshold OR max iters)
         no → re-render          PAUSE POINT → human / email digest
                                       ▼ yes
        PROMOTE → DOCS PAGE (prose) + hero video on top
                  → external-release gate → publish for users
```

## Component map — reuse vs build

### Reuse (do not rebuild)

| Stage | Existing tool |
|---|---|
| Evidence audit / context ingestion (Phase 0) | canopy `context-ingestion` + ACE `program-input-sweep` patterns; Drive MCP; project memories |
| Why-grounding stress test (Phase 0) | ACE `idea-to-pdd` + `idea-to-pdd-qa` + `idea-to-pdd-eval` (5-question stress test) |
| Iterating renderer (drive live app, screenshot scenes) | canopy `walkthrough` skill + `browse` |
| Seed product state for the demo | connect-labs synthetic chain (`synthetic-narrative-plan → data-generate → workflow-seed`) + freshness guard |
| Video-quality verdict | `visual-judge` (Tough Judge) + walkthrough/video-spec eval dims |
| Route product fixes from scene failures | canopy walkthrough `improve` agent routing (`/review`, `/design-review`, `/qa`) — extend its triggers |
| Polished final video | connect-labs Playwright recorder rig (narrated MP4); ACE Remotion as the glossy option (deferred) |
| Verdict shape, QA-gates-eval, run state, pause points | ACE verdict schema + `run_state.yaml` + Pause Points convention |
| Autonomy, digest email, cross-run learnings | canopy PM autonomous loop (`scout→propose→implement→learn`, `.canopy/` persistence) |
| Editable web review page | ACE clip-explorer (already an editable web video editor) + canopy-web hosting |
| Docs-page generation + sync | `document-release` / canopy `doc-regeneration` patterns; `docs-vs-code-review` to keep page↔code honest |
| Share/host the artifact | `walkthrough-share` → canopy-web |

### Build new (the real gaps)

0. **Why-development tooling** (the front phase — built first for the rooftop exercise). An **evidence-audit** step that ingests prior docs/code/research into an inventory; a **why-brief author** that drafts the why-spine with evidence links + `RESEARCH`/`CAPABILITY`/`DECISION` gap tags; a **why stress-test** (`-qa` + `-eval`) adapting `idea-to-pdd`; and a **gap router** that sends research to autonomous investigation, capability gaps to the build loop, and decision gaps to the concept gate. Output: the grounded why-brief that becomes the narration spine. *See Phase 0 above.*

1. **Unified spec** — extend the canopy walkthrough YAML with per-scene fields: `concept_claim` (what the scene asserts the product does and why it matters), `provenance` (link back to the why-brief rationale + evidence/capability), and `design_intent` (the design decision under test). This one file is simultaneously the design doc and the video script. *New schema + small authoring skill.*

2. **Concept/design judge** — the centerpiece. Watches the rendered walkthrough and scores **concept clarity, design soundness, why-groundedness, claim↔reality coherence, motion-surfaced friction**, and emits structured `design_findings[]`, each tagged `→ PRODUCT` (build/fix) / `→ CONCEPT` (rethink idea or narration) / `→ RESEARCH` (investigate to update the why-brief) / `→ DEFER`. Claim↔reality is scored and surfaced but **non-blocking**. *New eval skill + rubric (provisional, calibrated after 3 real runs).*

3. **Dual-verdict orchestrator agent** — runs the cycle, fans the two judges in parallel, routes each finding to the right fixer, manages pause points, writes the digest email. Built as a canopy agent reusing `improve`-mode routing + PM-loop autonomy.

4. **Unified-spec QA gate** — binary structural checks (every scene has a falsifiable `concept_claim`, personas resolve, etc.) that gate the judges. Mirrors ACE `-qa`.

5. **Promotion + docs-page builder** — at convergence, (a) transform the converged unified spec into a render spec for the polished video (narrated recording for live features) and (b) generate/update the **documentation page** from the same why-brief + unified spec, with the hero video embedded on top. The page is a tracked build artifact that co-evolves each run and is published at the external-release gate. Reuses doc-generation patterns (`document-release` / canopy `doc-regeneration`); page+video are graded together as the user-onboarding artifact.

6. **Review surface + escalation policy** — *the most important new piece for the primary user.* A shared `review_request` artifact per pause, rendered two ways: inline `AskUserQuestion` and an **editable web review page** (the playing cut + ≤3 forked decisions with the pick pre-selected + inline-editable narration + a collapsed autonomous-audit + Approve-&-continue). Enforces the two-gate blocking policy (concept change, external release) and the suggest-then-confirm self-tuning. Reuses the ACE clip-explorer / canopy-web editable surfaces + PM-loop digest email. *See Review model above.*

### Deferred (YAGNI for v1)

- Concept-judge **calibration fixtures** (defect-creator analog) — ship the rubric provisionally, calibrate after 3 real runs (ACE precedent).
- **Remotion glossy** render path — the labs narrated recorder is enough for rooftop surveys.
- Standalone-plugin scaffolding — v1 lives on canopy.

## The two verdicts (rubric sketch)

**Concept verdict** (LLM judge, weighted dims, own threshold):
- Concept clarity — would a smart outsider get what this is and why it matters?
- Design soundness — does the idea hold together; are the interactions coherent?
- **Why groundedness** — does each `concept_claim` trace to a real why-brief rationale + evidence/capability (the provenance chain), or is it asserted on air?
- Claim↔reality coherence — does the footage actually demonstrate the narration's claims? (scored, non-blocking)
- Motion-surfaced friction — what's clunky/slow/confusing/backwards when seen moving?
- Output: `design_findings[]` with `route` tag (`PRODUCT` / `CONCEPT` / `RESEARCH` / `DEFER`) + severity.

**User-artifact verdict** (docs page + hero video, prospective-user lens): reuse Tough Judge / video-spec eval dims, plus does-a-new-user-understand-the-capabilities. Dims: capability coverage, clarity-for-a-newcomer, why-comes-through, page↔video coherence, polish/demo-readiness.

Both follow the ACE verdict schema (`overall_score`, `dimensions{}`, `verdict`, `auto_surfaced[]`, `gate{}`). QA failure skips both judges.

## Routing

- Concept `→ PRODUCT` findings → TDD product fixes (existing `improve` specialists: `/design-review`, `/review`, `/qa`).
- Concept `→ CONCEPT` findings → edit the unified spec's narration/`design_intent`, possibly re-open the design.
- Concept `→ RESEARCH` findings → autonomous investigation that updates the why-brief evidence (a `CAPABILITY` gap instead spawns product-build work).
- User-artifact findings → spec/scene/pacing fixes **and docs-page prose fixes** (same grounded source, so a fix can touch narration, page, or both).
- Re-render only what changed; converge when both verdicts ≥ threshold OR max iterations, else **pause** and digest.

## Autonomy & pause points

See **Review model** for the full policy. In short: the loop runs autonomously overnight per the PM loop and halts at only two gates — **concept definition/change** and **external release**. A `design_finding` re-summons the user only if it implies a concept change, not an execution gap. Every pause emits the email digest + editable review page; everything autonomous lands in the digest's audit, non-blocking. Cross-run learnings persist so resolved findings aren't re-raised, and rubber-stamped decision classes are proposed for downgrade.

## First run: Rooftop Surveys

The first real output is **Phase 0 for rooftop sampling itself** — not just framework plumbing. Concretely: audit what we already have (the rooftop-surveys app design doc, the labs sampling engine, the R pipeline + decoded Stage A params in Drive, the Connect-microplanning integration) into an evidence inventory; draft the grounded why-spine for *how regions are selected, what the settings are, and why those settings*; surface the `RESEARCH`/`CAPABILITY`/`DECISION` gaps that grounding exposes; and stress-test it. That grounded why-brief then drives the unified spec and the walkthrough.

This means building the Phase 0 tooling and using it for genuine rooftop discovery in the same pass — the tool and its first real product output get built together. Where the synthetic chain and recorder rig already exist, reuse them for the render/promote stages.

**Rooftop narrative seed (to be iterated before we dive in).** The compelling story starts a step *before* sampling rooftops — with **how to choose which areas to target at all**, for either a one-time survey or a **difference-in-differences (DiD) continuous-monitoring** design (treatment vs comparison areas tracked over time). The arc that motivates it: **visualize existing Connect service-delivery data** → inspect the **administrative boundaries** that the visualization suggests are worth targeting → select boundaries → *then* rooftop-sample within them. This already implies candidate `CAPABILITY` gaps (visualize Connect delivery data; browse/select admin boundaries; support a DiD treatment/comparison design) and `DECISION` gaps (survey-only vs DiD; which boundaries) — exactly the kind of gaps Phase 0 is built to surface. Per the user's direction: **build the framework generically first; iterate this specific rooftop example before diving into the rooftop build.**

**Success =** (a) Phase 0 produces a grounded, evidence-backed why-brief for rooftop sampling that exposes at least one real research-or-capability gap we then close; (b) the loop surfaces at least one real rooftop design finding we fix; (c) it emits a shippable explainer at convergence.

## Open questions

- Concept-verdict threshold + max-iteration count (start: ≥4/5 both, 3 iters — match walkthrough `improve`).
- Exact home/namespace for the new skill family on canopy.
- Whether the unified spec replaces or wraps the existing `docs/walkthroughs/*.yaml`.
- `review_request` schema, and how web-page edits (narration rewrites, fork redirects) flow back into `run_state.yaml` and the cross-run learnings.
- Self-tuning threshold N (how many rubber-stamps before proposing a downgrade).
- Where the docs page lives and how it's published (connect-labs docs site? ace-web? a dedicated capabilities page?), and the why-brief schema + evidence-link format that feeds narration, video, and page from one source.

## v3 evolution: actionable narratives (2026-05-28, from dogfooding)

The first rooftop dogfood surfaced that the unified spec's per-scene `concept_claim` is *marketing prose, not buildable* — "you couldn't act on it." Three changes make the narrative the actual build spec:

1. **A narrative chunk is a self-composed, verifiable feature set.** Each scene gains `features[]`: each feature is a concrete buildable unit `{id, description, verify}` where `verify` is how you validate it's done (an API assertion, a UI state, a test). The narration stays the human story; the features are the actionable spec. The union of all scenes' features = the demo's **burn-down list** — build a feature → run its `verify` → check it off; the demo is done when the list burns down and the walkthrough renders + judges pass.

2. **Actionability eval (`ddd-narrative-actionability-eval`) — the load-bearing new gate.** Tests "how well can an AI understand what to build next from this narrative?" Method (chosen): **cold-derive vs declared features + self-consistency** — a fresh AI reads ONLY a chunk's narration, writes the build plan it infers, and we score that against the chunk's declared `features[]` (coverage / specificity / correctness); run N times so divergence flags ambiguity. Low score ⇒ the narrative is too vague to act on ⇒ revise *before* the human reviews it. Runs every iteration; **gates the narrative-review** (the user only reviews narratives that are buildable by construction).

3. **Clearer review UI.** The `agree / edit / rethink` triple was incoherent (inline-edit made "edit" redundant; "rethink" was undefined). Collapse to: inline-edit narration (always) + ONE decision — **Approve & continue** (lock, applying edits) vs **Send back to re-draft** (the framing/approach is wrong — the AI re-authors, not just rewords). The review page also shows each chunk's `features[]` and its actionability score.

Loop becomes: `ddd-spec` (author with features) → `ddd-spec-qa` → **`ddd-narrative-actionability-eval`** (buildable?) → narrative-review (user agrees) → build + burn down features (each `verify`) → render → judges.
