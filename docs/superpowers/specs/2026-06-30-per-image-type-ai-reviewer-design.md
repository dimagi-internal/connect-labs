# Per-Image-Type AI Reviewer Selection — Design

**Date:** 2026-06-30
**App:** `audit/`
**Status:** Approved design, pending implementation plan

## Problem

When an audit covers multiple image types, the auditor cannot say *which AI
reviewer should run on which image*. Today the wizard has two **independent**
steps:

- **Image-type selection** — checkboxes choosing which image question paths
  (`question_id`s) to audit. This scopes which visits are included.
- **AI agent selection** — a single global dropdown. The chosen agent runs on
  **every** image of **every** selected type.

The agents are written for specific image types by intent
(`ScaleValidationAgent` for scale/weight photos, `MUACOverzoomAgent` for MUAC
photos) but nothing binds an agent to the type it was built for. So selecting
two image types plus one agent runs that agent on images it was never meant to
review.

A second, related defect: the scale agent needs a *comparison value* (a weight
reading from a form field). That value only reaches the agent if the auditor
*separately* defines a "related field rule" mapping a field path to the image.
Nothing connects "I picked the scale agent" to "I must tell it where the value
comes from." A user can pick the scale agent, skip the field rule, and images
are **silently skipped** at review time. `requires_reading` is not even sent to
the frontend, so the UI cannot prompt for it.

## Goals

1. Pair an AI reviewer with an image type **at creation time**, so each selected
   image type runs only its chosen reviewer.
2. Make a reviewer's required configuration (e.g. the scale agent's comparison
   field) appear **as a setting of that reviewer**, via progressive disclosure —
   killing the silent-skip footgun.
3. Merge the two separate wizard steps (image types + AI agent) into one
   coherent step.
4. Keep the door open for *multiple* reviewers per image type later, without
   building that behavior now.

## Non-Goals (deferred)

- Multiple AI reviewers actually running on one image. Storage, the run loop,
  and the review UI stay strictly **one AI verdict per image**. Revisited only
  when there is a real use case *and* the review UI is updated to show multiple
  verdicts.
- Agent→image-type affinity / auto-suggestion. Every registered agent is listed
  in every type's reviewer dropdown; the author picks.

## Locked Requirements

1. **One reviewer per image type** in the v1 UI.
2. **Merge** image-type selection and AI-agent selection into a single wizard
   step.
3. **All agents** listed in each type's reviewer dropdown (no affinity guessing).
4. **Progressive disclosure** — choosing an agent reveals *that agent's* settings
   (scale → a comparison-field picker; MUAC → nothing).
5. **Absorb** the reading/comparison field into agent config; keep a slim
   optional "context fields" control for agent-less field display to human
   reviewers.
6. Strictly **one AI review per image**; defer all multi-reviewer behavior. Only
   the creation payload is shaped (list of reviewers) so a second reviewer is an
   additive change later — nothing downstream behaves as multi.

## Design

### 1. Agents declare their own config schema (Approach B — fully declarative)

`BaseAIReviewAgent` gains an optional class attribute:

```python
config_fields: list[dict] = []   # declarative settings the wizard renders
```

`ScaleValidationAgent`:

```python
config_fields = [
    {
        "key": "comparison_field",
        "label": "Manual Scale Value",
        "type": "form_field",     # → renders a form-field picker
        "required": True,
        "help": "Form field whose value is compared against the scale photo",
    }
]
```

`MUACOverzoomAgent`: `config_fields = []`.

**Field-type vocabulary.** v1 implements exactly one renderer, `form_field`
(a dropdown of the opportunity's form-field paths). The `type` discriminator is
the extension point: a future agent can declare `{"type": "number", ...}` or
`{"type": "select", "options": [...]}` and the wizard grows one renderer with no
rewrite. Unknown `type` values render nothing (forward-safe) and log a console
warning.

**Naming.** `comparison_field` is the author/config/payload-facing name (chosen
for clarity — the agent compares the photo against this field's value). The
internal runtime plumbing is unchanged: the value is still delivered to the
agent as `form_data["reading"]`, and `requires_reading` stays on the agent.
Renaming the runtime plumbing is out of scope for v1.

**Surfaced to the frontend.** `AIAgentsListAPIView` adds `config_fields` to each
agent's JSON, alongside the existing `name`, `description`, `result_actions`,
`auto_apply_result`. The wizard reads it and renders settings generically.

### 2. Merged wizard step (replaces today's two steps)

A single step: a list of available image types, each with its reviewer and that
reviewer's settings inline.

```
┌─ Step: Images & AI Review ────────────────────────────────┐
│ ☑ form/scale_photo            [scale_photo]                │
│      AI reviewer:  [ Scale Image Validation ▾ ]            │
│      └ Manual Scale Value: [ form/child_weight ▾ ]         │
│      └ Auto-apply: ☑ Pass matched  ☑ Fail unmatched        │
│                                                            │
│ ☑ form/muac_photo             [muac_photo]                 │
│      AI reviewer:  [ MUAC OverZoom ▾ ]                     │
│      (no extra settings)                                   │
│                                                            │
│ ☐ form/consent_photo          [consent_photo]             │
│                                                            │
│ ▸ Context fields (optional) — show extra form values to    │
│   reviewers without an AI agent                            │
└────────────────────────────────────────────────────────────┘
```

Behavior:

- Checking an image type reveals its reviewer dropdown (default "None — skip AI
  review"). All registered agents are listed.
- Choosing an agent renders its `config_fields` generically beneath it. Scale →
  the required "Manual Scale Value" form-field picker; MUAC → nothing.
- The agent's auto-apply actions render under it, scoped to *that* type's
  reviewer (replacing the single global auto-apply block).
- One reviewer per type in the UI; the per-type config is list-shaped internally
  so a second reviewer is an additive change later.
- **Context fields** — a collapsed optional control to attach arbitrary field
  values to images for the human reviewer, agent-less. This is the surviving
  slice of today's manual "related field rules," minus the comparison-value job
  (now an agent setting).

**In-wizard validation.** If a type's chosen agent has a `required` config field
left blank (scale agent with no Manual Scale Value), the step blocks advancing
with an inline message. This is the silent-skip fix.

### 3. Creation payload + backend translation

Payload replaces the flat `ai_agent_id` / `ai_auto_apply_actions` / detached
`relatedFields` with one per-type structure:

```jsonc
{
  "opportunities": [...],
  "criteria": { ... },
  "image_audits": [
    {
      "image_path": "form/scale_photo",
      "reviewers": [                          // list-shaped; v1 emits 0 or 1
        {
          "agent_id": "scale_validation",
          "config": { "comparison_field": "form/child_weight" },
          "auto_apply_actions": ["pass_matched", "fail_unmatched"]
        }
      ]
    },
    { "image_path": "form/muac_photo",
      "reviewers": [ { "agent_id": "muac_overzoom", "config": {}, "auto_apply_actions": ["fail_overzoomed"] } ] },
    { "image_path": "form/consent_photo", "reviewers": [] }
  ],
  "context_fields": [
    { "image_path": "form/scale_photo", "field_path": "form/child_id", "label": "Child ID" }
  ]
}
```

- `image_audits[].image_path` **is** the image-type selection — it carries the
  "include only visits with this image" filter the checkbox provided before.
- `reviewers` is a list but the v1 UI emits at most one. The backend runs
  whatever is in the list, so it is not *blocked* on multi; it just is not fed
  more than one.

**Backend translation (create view).** Translate `image_audits` + `context_fields`
into the **existing internal shapes** so downstream machinery is untouched:

1. Each `image_path` → a `related_fields` rule `{imagePath, filterByImage: true}`
   (same visit-filtering as today).
2. Each reviewer `config.comparison_field` → a `related_fields` reading rule
   `{imagePath, fieldPath: comparison_field}` (this is what attaches `reading`
   to the image; the agent code does not change).
3. Each `context_fields` entry → a `related_fields` display rule (no filter),
   preserving human-context display.
4. Build a **`question_id → reviewer`** map from `image_audits`, persisted on the
   session as new `data["ai_reviewers"]`, so the async task knows which agent
   runs on which image type. Each entry: `{agent_id, auto_apply_actions}`.

**Run loop (`tasks.py:_run_ai_review_on_sessions`).** Replace the single global
`get_agent(ai_agent_id)` with a per-work-item lookup by `question_id`:

```python
reviewer = ai_reviewers.get(question_id)      # {agent_id, auto_apply_actions}
if not reviewer:
    continue                                   # this image type has no AI reviewer
agent = get_agent(reviewer["agent_id"])
```

Auto-apply mapping is read from that per-type reviewer, not a global list.
**Results storage is unchanged** — one verdict per `blob_id`. Since v1 runs
exactly one reviewer per image, there is no collision.

Everything below the run loop is unchanged: image download, `ReviewContext`,
agent code, `set_assessment`, and the render/review UI.

### 4. Form-field picker endpoint

The "Manual Scale Value" picker needs real options instead of free text. Add
`OpportunityFieldQuestionsAPIView` at
`api/opportunity/<opp_id>/field-questions/` — a sibling to the existing
`image-questions` view, using the **same streaming visit sampler**, but
flattening `form_json` leaf scalar paths (a new `extract_field_paths` helper)
instead of image paths. Returns `[{id, label, path}, ...]`.

Fallback: if the endpoint errors, the picker degrades to a free-text input
(today's behavior), so creation is never blocked.

## Error Handling / Edge Cases

- **Required config left blank** — wizard blocks step advance with inline error.
  Backend also defensively skips a reviewer whose required `config` is missing,
  logging `[AIReview] skipped <agent> on <qid>: missing comparison_field`.
- **Image type checked, reviewer = None** — valid: visits filtered in, no AI
  runs (human-only audit of that type).
- **Reviewer mapped to a type with zero sampled images** — allowed; produces no
  work items.
- **Backward compatibility** — `image_audits` is a new payload key. The create
  view keeps accepting the legacy `ai_agent_id` + `relatedFields` shape
  (translated to the internal form as today) so in-flight clients / saved flows
  do not break. The new wizard always sends `image_audits`.
- **Unknown `config_fields[].type`** — wizard renders nothing for it
  (forward-safe) and logs a console warning.

## Testing

**Unit**
- `AIAgentsListAPIView` surfaces each agent's `config_fields`.
- `image_audits` → `related_fields` + `ai_reviewers` translation: scale (with
  `comparison_field`), MUAC (none), type-with-no-reviewer, `context_fields`.
- `extract_field_paths` flattens `form_json` leaf paths correctly.
- Run-loop per-`question_id` agent lookup: right agent per type; skip when no
  reviewer; skip when required config missing.
- Legacy `ai_agent_id` payload still translates and runs.

**Integration**
- Full create payload → session has correct `ai_reviewers` map + translated
  `related_fields`.
- Run task tags each image's verdict from the type-appropriate agent.

**Manual / browser**
- `gstack browse` the wizard on labs after deploy: inline progressive
  disclosure, required-field block, context-fields collapse.

## Affected Files (anticipated)

- `commcare_connect/labs/ai_review_agents/base.py` — `config_fields` attribute.
- `commcare_connect/labs/ai_review_agents/agents/scale_validation.py` — declare
  `comparison_field`.
- `commcare_connect/audit/views.py` — `AIAgentsListAPIView` (surface
  `config_fields`); create view (`image_audits`/`context_fields` translation +
  legacy compat); new `OpportunityFieldQuestionsAPIView`.
- `commcare_connect/audit/urls.py` — `field-questions/` route.
- `commcare_connect/audit/analysis_config.py` — `extract_field_paths` helper.
- `commcare_connect/audit/data_access.py` — persist `ai_reviewers` on the
  session.
- `commcare_connect/audit/tasks.py` — per-`question_id` agent lookup in
  `_run_ai_review_on_sessions`.
- `commcare_connect/templates/audit/audit_creation_wizard.html` — merged step,
  generic `config_fields` renderer, context-fields control, validation.
