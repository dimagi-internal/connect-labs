# Demo-Driven Development: `/walkthrough` Skill

**Date:** 2026-03-23
**Status:** Approved
**Branch:** emdash/solicitation-review-1xh

## Problem

Traditional testing answers "does the code work?" but not "would I be proud to show this to a stakeholder?" After building a complex feature (like the solicitation lifecycle with AI), there's no systematic way to:

1. See the full product experience through each persona's eyes
2. Evaluate whether AI outputs are actually good (not just valid JSON)
3. Generate stakeholder-ready artifacts without manual screenshotting
4. Validate incidental improvements (UI polish, form updates) alongside the primary feature
5. Iterate on the experience before walking into the room

## Solution: Demo-Driven Development

A workflow where **the demo is designed alongside the feature** and a `/walkthrough` skill generates **stakeholder-ready HTML presentations** by executing the demo against the live app.

### Core Concept

```
DESIGN PHASE                    IMPLEMENTATION              DEMO PREP
────────────────────────        ──────────────              ─────────
Brainstorm → Design Doc    →    Build the feature    →    /walkthrough baobab-demo
  └── "Demo Narrative Arc"                                    │
       section (already exists                                ▼
       in CEO plans)                                   HTML deck generated
                                                              │
                                                              ▼
                                                       Review slides
                                                       Fix issues → rerun
                                                              │
                                                              ▼
                                                       STAKEHOLDER MEETING
                                                       (project the deck
                                                        or export to PDF)
```

### Distinction from `/qa`

| | `/qa` | `/walkthrough` |
|--|-------|----------------|
| **Goal** | Find bugs | Validate the experience |
| **Output** | Bug report | Presentation deck |
| **Audience** | Developer | Stakeholders |
| **AI evaluation** | "Does it work?" | "Is it good enough to show?" |
| **Tone** | Test results | Product story |

## Architecture

Three components:

### 1. Demo Spec (YAML)

A lightweight, story-focused definition. Claude generates it from the design doc's "Demo Narrative Arc" section or from conversation. No selectors, no CSS, no waits — Claude figures out execution details at runtime.

```yaml
# docs/walkthroughs/baobab-demo.yaml
name: "Baobab Regranting Platform Demo"
narrative: "Connect serves both sides — funders get an AI analyst, applicants get an AI coach"

personas:
  sarah:
    name: "Sarah Chen"
    role: "Senior Program Manager, Dimagi"
    color: "#2563eb"
    intro: "Sarah manages programs and sources implementing partners."
  amina:
    name: "Amina Okafor"
    role: "Program Director, Health Bridge Nigeria"
    color: "#059669"
    intro: "Amina leads a community health org. She responds to funding opportunities."
  james:
    name: "James Mwangi"
    role: "Technical Advisor, Dimagi"
    color: "#d97706"
    intro: "James evaluates responses and manages selected LLOs post-award."

scenes:
  - persona: sarah
    title: "Here's your fund"
    show: "Fund dashboard with KPIs, charts, delivery map — all loaded and interactive"
    impressive_because: "Data streams in real-time, KPIs animate, map shows actual grantee locations"

  - persona: sarah
    title: "Post a solicitation with AI criteria"
    show: "Create an RFP describing a CHW training program, generate evaluation criteria with AI"
    impressive_because: "AI writes relevant, well-weighted criteria she'd spend hours on manually"
    ai_quality: "Criteria specific to CHW training scope, not generic grant criteria"

  - persona: amina
    title: "AI helps applicants write stronger responses"
    show: "Amina sees solicitation with criteria upfront, starts a response, gets AI coaching"
    impressive_because: "Coach gives specific feedback tied to evaluation criteria — tells her how to score higher"
    ai_quality: "Coaching must reference specific criteria, not just say 'add more detail'"

  - persona: james
    title: "AI reviews all responses"
    show: "James triggers AI comparative review of all submitted responses"
    impressive_because: "AI ranks applicants, flags risks, recommends a shortlist — like having a grants analyst"
    ai_quality: "Analysis is comparative (not per-response), flags real differentiators between applicants"

  - persona: james
    title: "Select your grantees"
    show: "James awards the winning response, allocation auto-created"
    impressive_because: "One click from review to award — budget allocated, org assigned"

  - persona: sarah
    title: "Monitor delivery"
    show: "Return to fund dashboard, filter by delivery type and region, explore charts interactively"
    impressive_because: "Cross-chart filtering lets Sarah explore the data like a dashboard, not a static report"

  - persona: sarah
    title: "Forecast your impact"
    show: "Delivery pace chart showing projected completion date"
    impressive_because: "Positive framing — 'on track to deliver full impact by August' — not burn rate language"

  - persona: sarah
    title: "Report to Bloomberg"
    show: "AI generates narrative report, export to PDF one-pager"
    impressive_because: "AI writes the donor report in minutes — highlights, risks, recommendations"
    ai_quality: "Report should reference specific grantee performance, not generic language"
```

Key design choices:
- **No execution details** — no selectors, waits, or actions. Claude figures those out.
- **Story-focused** — `show` and `impressive_because` describe the experience, not the mechanics.
- **AI quality criteria** — specific, evaluable statements about what "good" looks like for each AI feature.
- **Claude generates this file** — from design doc narrative arcs or conversation. User iterates on the story, not the YAML.

### 2. Execution Engine (`/walkthrough` Skill)

A Claude Code skill that reads the demo spec, executes it using the `browse` tool, and generates the presentation.

**Execution flow:**

```
/walkthrough baobab-demo
     │
     ▼
┌─ READ SPEC ──────────────────────────────────────────┐
│  docs/walkthroughs/baobab-demo.yaml                  │
│  Parse scenes, personas, quality criteria             │
└──────────────────────┬───────────────────────────────┘
                       ▼
┌─ SETUP ──────────────────────────────────────────────┐
│  Create test data if needed (solicitation, responses) │
│  Authenticate as first persona via test-auth endpoint │
└──────────────────────┬───────────────────────────────┘
                       ▼
┌─ EXECUTE SCENES ─────────────────────────────────────┐
│  For each scene:                                      │
│    1. Switch persona if needed (re-authenticate)      │
│    2. Navigate to the relevant page                   │
│    3. Perform actions (fill forms, click, trigger AI) │
│    4. Wait for results (AI streaming, data loading)   │
│    5. Take annotated screenshots at key moments       │
│    6. If ai_quality defined: evaluate AI output       │
│    7. Collect: screenshots, AI eval, timing, issues   │
│                                                       │
│  Claude adapts in real-time — finds elements by       │
│  context, waits for AI to finish streaming, handles   │
│  unexpected states.                                   │
└──────────────────────┬───────────────────────────────┘
                       ▼
┌─ GENERATE PRESENTATION ──────────────────────────────┐
│  Build HTML slideshow from collected artifacts        │
│  Write to screenshots/walkthroughs/baobab-demo.html  │
│  Open in browser for review                          │
└──────────────────────────────────────────────────────┘
```

**Key behaviors:**
- **Browser tool:** Uses the gstack `browse` skill — a headless Chromium browser with ~100ms per command. Navigate URLs, click elements, fill forms, take screenshots, assert element states.
- **Persona switching:** Purely narrative — all personas use the same test account (`jjackson+test`). The walkthrough switches which pages to visit and what actions to take per persona, not OAuth sessions. The persona name/role appears on slides to tell the story.
- **AI evaluation:** Claude reads AI-generated content via DOM text extraction (browse tool's text/element reading), evaluates it against the `ai_quality` rubric from the spec. Produces a quality score (1-5) and analyst commentary.
- **Adaptation:** If a button isn't where expected, Claude looks for it. If AI is still streaming, Claude waits. If test data is missing, Claude creates it.

**AI quality scoring calibration:**
- **5/5** — Output is specific to the context, references concrete details, would impress a stakeholder
- **4/5** — Good quality, relevant, but missing one specific detail or slightly generic in one area
- **3/5** — Correct but generic — could apply to any similar solicitation/program
- **2/5** — Partially relevant but contains irrelevant or confusing content
- **1/5** — Wrong, empty, or completely generic boilerplate

**Failure modes:**
- **Scene fails (element not found, page error):** Screenshot the error state, log the issue, skip to next scene. Partial decks are valid — better to show 6/8 scenes with 2 noted failures than nothing.
- **AI evaluation timeout:** Skip the quality assessment, note "AI did not respond" on the slide.
- **Data setup fails:** Abort early with clear error message about what's missing.

**Data setup:**
Each walkthrough run creates fresh test data by driving the web UI with browse:
- Solicitations created via the create form (browse fills and submits)
- Responses created via the response form (browse fills and submits)
- Reviews created via the review form (browse fills and submits)
- Fund/allocation data uses existing test data already in the labs environment
- No cleanup needed — test data accumulates harmlessly in the labs environment (known trade-off, acceptable for a labs/demo environment).

### 3. HTML Presentation Output

A single, self-contained HTML file: `screenshots/walkthroughs/baobab-demo.html`

**Slide types:**

**Title Slide:**
```
┌──────────────────────────────────────────────────────┐
│                                                      │
│        BAOBAB REGRANTING PLATFORM DEMO               │
│                                                      │
│  "Connect serves both sides — funders get an AI      │
│   analyst, applicants get an AI coach"               │
│                                                      │
│  Generated: March 23, 2026                           │
│  8 scenes · 3 personas · 4 AI features evaluated     │
│                                                      │
└──────────────────────────────────────────────────────┘
```

**Persona Intro Slide** (one per persona, shown before their first scene):
```
┌──────────────────────────────────────────────────────┐
│  ┌──────┐                                            │
│  │ ◉ SC │  Sarah Chen                                │
│  └──────┘  Senior Program Manager, Dimagi            │
│                                                      │
│  "Sarah manages programs and sources implementing    │
│   partners. She wants to create a professional       │
│   solicitation quickly."                             │
│                                                      │
└──────────────────────────────────────────────────────┘
```

**Scene Slide:**
```
┌──────────────────────────────────────────────────────┐
│  ◉ Sarah Chen                        [3/12]  ▸▸▸○○  │
│                                                      │
│  "Post a solicitation with AI criteria"              │
│                                                      │
│  Sarah creates an RFP describing a CHW training      │
│  program, then has AI generate evaluation criteria.  │
│                                                      │
│  ┌────────────────────────────────────────────────┐  │
│  │                                                │  │
│  │              [SCREENSHOT]                      │  │
│  │              Full-width, high-res              │  │
│  │                                                │  │
│  └────────────────────────────────────────────────┘  │
│                                                      │
│  ┌─ AI Quality ──────────────────────────────────┐   │
│  │  ★★★★☆  Criteria are relevant to CHW training │   │
│  │  and well-weighted. Scoring guides are clear.  │   │
│  │  "Value for Money" criterion could be more     │   │
│  │  specific to the West African context.         │   │
│  └────────────────────────────────────────────────┘   │
│                                                      │
│  ← →  navigate                                       │
└──────────────────────────────────────────────────────┘
```

**Summary Slide:**
```
┌──────────────────────────────────────────────────────┐
│  WALKTHROUGH SUMMARY                                 │
│  Run: 2026-03-23 14:30 | Duration: 3m 42s           │
│                                                      │
│  Scenes: 8/8 completed                               │
│                                                      │
│  AI Quality Scores:                                  │
│    Criteria Generation:    ★★★★☆  (4/5)             │
│    Application Coach:      ★★★★★  (5/5)             │
│    Comparative Review:     ★★★☆☆  (3/5) ← focus    │
│    Bloomberg Report:       ★★★★☆  (4/5)             │
│                                                      │
│  Issues Found:                                       │
│    ⚠ Scene 4: Coach response time 12s (slow)        │
│    ⚠ Scene 6: Delivery map — no markers (test data) │
│                                                      │
│  vs. Previous Run (if available):                    │
│    ↑ Criteria quality improved (3→4)                 │
│    = Review quality unchanged                        │
│                                                      │
└──────────────────────────────────────────────────────┘
```

**Technical requirements:**
- **Single HTML file** — screenshots embedded as base64 (compressed JPEG at 80% quality, max 1440px wide to keep file under 10MB), inline CSS/JS
- **Keyboard navigation** — left/right arrows, progress indicator
- **Print CSS** — `Ctrl+P` produces a clean PDF (one slide per page)
- **Professional design** — clean typography, generous whitespace, not a developer tool
- **Responsive** — works on projector (1920x1080) and laptop screens
- **Portable** — email the HTML file, open anywhere, no server needed

**Run history:**
Each run also writes a JSON sidecar: `screenshots/walkthroughs/baobab-demo.json` with run metadata (timestamp, duration, scene results, AI quality scores). The "vs. Previous Run" comparison on the summary slide reads this file from the prior run. If no prior run exists, the comparison section is omitted.

## Workflow Integration

### Input: From Design Docs

The demo spec is generated from the "Demo Narrative Arc" section that already exists in CEO plans. The `/walkthrough` skill supports two modes:
- **`/walkthrough baobab-demo`** — Execute an existing walkthrough spec
- **`/walkthrough generate`** — Generate a new walkthrough spec from a design doc or conversation (interactive — Claude asks what to show, writes the YAML)

The YAML is the structured version of what's already in the design doc's narrative arc.

### Output: Stakeholder-Ready Deck

The HTML presentation is the primary output. It's designed to be:
- **Reviewed locally** — scroll through, note issues, fix, rerun
- **Shown to stakeholders** — project in a meeting or share the file
- **Exported to PDF** — `Ctrl+P` for a clean printable version
- **Compared across runs** — summary slide tracks quality scores over time

### Rerun Workflow

```
/walkthrough baobab-demo
  → Review: "Slide 4 criteria too generic, slide 7 map empty"
  → Fix code / adjust prompts / add test data
  → /walkthrough baobab-demo  (rerun)
  → Compare: criteria improved (3→4), map now populated
  → Ready for the room
```

## Generalizability

The system is feature-agnostic. Any feature with multiple personas, a multi-step workflow, AI-generated content, or stakeholder visibility can use `/walkthrough`.

**Future work (not in scope for v1):**
- Integration with brainstorming skill to generate demo specs during feature design
- Additional walkthroughs: `audit-lifecycle.yaml`, `workflow-demo.yaml`, `onboarding.yaml`
- Multi-profile persona switching (separate OAuth sessions per persona)

## First Implementation: Baobab Demo

The first walkthrough is `baobab-demo.yaml` targeting the 8-scene narrative arc from the CEO plan. This proves the concept and directly serves the upcoming Baobab meeting.

**What "done" looks like:** Running `/walkthrough baobab-demo` produces an HTML deck that you'd be comfortable projecting in the Baobab meeting.
