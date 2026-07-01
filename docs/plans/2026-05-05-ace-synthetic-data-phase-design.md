# ACE Phase 6 — Synthetic Data and Workflows

**Status:** Design — pending implementation plan
**Date:** 2026-05-05
**Touches:** ACE plugin (new phase + 7 skills + 4 evals), connect-labs (synthetic generator engine, two new SEED templates, 5 new MCP tools), persona catalog

## 1. Problem and goal

ACE today drives the full lifecycle of a Connect opportunity: design, app build, Connect setup, OCS, training, solicitation, execution, closeout. What it cannot do is **show the opp in action before it has an action.** Between training (Phase 5) and solicitation (currently Phase 6), there is no artifact a Dimagi staffer can hand to a prospective LLO, donor, or internal stakeholder that says "here is what this opportunity looks like running well."

The labs environment already supports a "synthetic opportunity" mode — flip a flag on a `SyntheticOpportunity` row, drop five fixture JSON files into a GDrive folder, and labs renders dashboards / pipelines / workflows against fictional data transparently. The dump-from-prod flow exists. **The generator does not.**

This design adds a new ACE phase that:

1. Generates a story-coherent synthetic dataset against the actual built apps' form schemas.
2. Stands up two demonstrative workflows on top of the synthetic data — one operational ("LLO weekly review of FLW performance with embedded coaching tasks") and one meta ("program admin audit of how the LLO is performing the operational review").
3. Polishes those workflows per-opp so the dashboards aren't generic.
4. Runs persona-tuned walkthroughs that produce stakeholder-ready slideshows.
5. Outputs a single summary artifact pointing at the live labs URL + the slideshows.

The same machinery is re-runnable on demand for new audiences and persona refreshes.

## 2. Non-goals

- **No solicitation integration in v1.** Phase 7 (solicitation-management) is being reworked separately; Phase 6 is self-contained.
- **No real OCS sessions.** Coaching conversations are embedded as transcript JSON on labs Task records and rendered chat-style by the workflow's task drawer. No external OCS coordination.
- **No production Connect mutations.** The opp in Connect stays exactly as Phase 3 left it. Synthetic mode is labs-only.
- **No new authentication surface.** All new MCP tools live in `connect_labs` MCP and reuse the existing PAT.
- **No automatic recurring regeneration.** Phase 6 fires once during the linear `/ace:run`, then re-runs are explicit (`/ace:step ...`).

## 3. Phase placement and renumbering

Phase 6 is inserted between current Phase 5 and the existing solicitation phase. All existing phase numbers ≥ 6 shift up by one.

| Old | New | Phase |
|-----|-----|-------|
| 1 | 1 | design-review |
| 2 | 2 | commcare-setup |
| 3 | 3 | connect-setup |
| 4 | 4 | ocs-setup |
| 5 | 5 | qa-and-training |
| — | **6** | **synthetic-data-and-workflows (new)** |
| 6 | 7 | solicitation-management |
| 7 | 8 | execution-manager |
| 8 | 9 | closeout |

Phase folder on disk: `ACE/<opp>/runs/<run-id>/6-synthetic/`.

**Why between 5 and 7:** by Phase 5 we have all inputs we need (PDD, app summaries, Connect config, expected journeys, app-test-cases, screenshots). Sliding earlier risks generating data before the schema is settled; sliding later means the asset isn't available before solicitation and we lose its biggest near-term use case.

**Phase 6 has no irreversible external action**, so the orchestrator does not pause at a Phase 6 gate. Fixture upload is reversible by the new `synthetic_disable` MCP tool.

## 4. Skill decomposition

Phase 6 is dispatched as a regular subagent (`agents/synthetic-data-and-workflows.md`) — none of its skills require level-0 Agent dispatch, so the standard subagent contract works. Skills run sequentially.

| # | Skill | Output | Eval |
|---|-------|--------|------|
| 1 | `synthetic-narrative-plan` | `synthetic-narrative-plan.md` + machine-readable manifest YAML | ✅ `synthetic-narrative-plan-eval` |
| 2 | `synthetic-data-generate` | run summary + 5 fixture JSON files + GDrive folder + `SyntheticOpportunity` row | ✅ `synthetic-data-generate-eval` |
| 3 | `synthetic-workflow-seed` | two workflow instances (LLO weekly + admin audit), Week 1 / Week 2 saved-runs, embedded synthetic OCS coaching tasks | ✅ `synthetic-workflow-seed-eval` |
| 3.5 | `synthetic-workflow-polish` | bespoke per-opp JSX layered over the scaffolds — hero panels, FLW story cards, anomaly callouts, chat-styled OCS drawer | ✅ `synthetic-workflow-polish-eval` |
| 4 | `synthetic-walkthrough-spec` | one walkthrough YAML spec per persona | ✅ `synthetic-walkthrough-spec-eval` |
| 5 | `synthetic-walkthrough-run` | per-persona HTML slideshow + scored screenshots | (canopy:walkthrough already scores per scene) |
| 6 | `synthetic-summary` | `synthetic-summary.md` — one-page reviewer-facing summary with labs URL, workflow URLs, slideshow links | (no eval — pure aggregator) |

`-eval` skills follow the canonical `verdict.yaml` shape and auto-dispatch unless `--no-evals` is passed, matching the rest of ACE.

### 4.1 Skill 1 — `synthetic-narrative-plan`

**Reads:** PDD, app summaries (Phase 2), expected-journeys.md (Phase 1), app-test-cases.yaml (Phase 2), connect-program-setup + connect-opp-setup (Phase 3 — payment units, deliver units, verification flags), CommCare HQ form schema via `get_form_json_paths(opportunity_id)`, persona catalog.

**Produces:** `synthetic-narrative-plan.md` (human-readable narrative — what story the data tells, why) + `synthetic-narrative-plan.yaml` (the manifest, see §5.1).

**Fallback (G2 mode):** If HQ schema is unreachable, the skill warns and degrades to PDD-only — manifest field paths come from the PDD's described forms, the eval flags reduced fidelity, and downstream skills proceed with looser schema validation.

### 4.2 Skill 2 — `synthetic-data-generate`

**Calls:** new MCP tool `synthetic_generate_from_manifest(opportunity_id, manifest_yaml)`. The tool delegates to the new connect-labs engine (§5.2) and returns the GDrive folder ID + record counts.

**Produces:** `synthetic-data-generate.md` (run summary), and the engine's outputs are also copied/symlinked into `6-synthetic/fixtures/` for review.

**Side effects:** GDrive folder created, 5 JSON files uploaded, `SyntheticOpportunity` row created or updated to point at the new folder with `enabled=True`. Old folders are not deleted (rollback / forensics).

### 4.3 Skill 3 — `synthetic-workflow-seed`

**Calls (in order):**
1. `workflow_create_from_template('llo_weekly_review', config={kpi_config, coaching_task_template})` — kpi_config and coaching_task_template come from the manifest.
2. Pipelines run once (existing labs job) to materialize Week 1 data.
3. For each FLW the manifest marks as triggering a coaching arc at Week 1: `task_create_synthetic(opportunity_id, assigned_to=<flw>, subject=<arc.subject>, ocs_conversation=<arc.transcript>)`.
4. `workflow_save_snapshot('<llo_weekly_review_id>', 'Week 1', captured_at=<week_1_end>)`.
5. Repeat 2–4 for Week 2 — by manifest design, some Week-1 FLWs improved (per their `improvement_arc`), some did not, new struggling FLWs appeared.
6. `workflow_create_from_template('program_admin_audit', config={watched_workflow_id: <llo_weekly_review_id>})`.

**Suitability check:** If the opp's KPI model doesn't fit per-FLW aggregation (rare — exotic opps where workers don't accumulate per-beneficiary metrics), the skill records a `scaffold_unsuitable: true` flag in its output and downstream polish does an L2-mode rewrite.

### 4.4 Skill 3.5 — `synthetic-workflow-polish`

**Reads:** scaffold render code via `workflow_get`, narrative plan, fixture summaries (specific FLW names + numbers to feature), opp branding cues if any (program name, domain language).

**Calls:** `workflow_patch_render_code` for surgical layered edits (preferred). Falls back to `workflow_update_render_code` for full rewrite if `scaffold_unsuitable: true`.

**Polish surface:**
- Hero panel with the opp's headline metric (e.g., "850 children measured · 92% data quality")
- Named FLW story cards ("Asha M. — 92% accuracy, mentored 3 peers this week")
- Seeded anomaly visually called out with a "needs attention" badge
- Week-over-week deltas styled as wins (green) / losses (red) / new flags (amber)
- OCS conversation drawer styled as a real chat with avatars, timestamps, message bubbles
- Brand cues consistent with the opp's domain (e.g., maternal health iconography for KMC opps)

**Internally** can leverage the existing `workflow-author` skill in connect-labs, which already knows how to iterate on live render code via the MCP.

**Eval:** grades visual quality (rendered screenshot judging via vision model), narrative-data coherence (do the highlighted FLW names actually appear in the data?), and brand fit (does the language match the opp's domain?).

### 4.5 Skill 4 — `synthetic-walkthrough-spec`

**Reads:** persona catalog (canned + opp-specific), narrative plan, workflow IDs, summary of seeded data.

**Produces:** one `synthetic-walkthrough-spec_<persona>.yaml` per persona — ordered scenes (URLs, click sequences, captions, "wow moment" assertions) calibrated to that persona's priorities. Wow moments correspond to seeded anomalies / coaching arcs from the manifest, so reviewer attention is drawn to data the manifest deliberately produced.

**Eval:** verifies every persona-priority dashboard is visited, wow moments map to manifest-seeded anomalies, scene order tells a coherent story.

### 4.6 Skill 5 — `synthetic-walkthrough-run`

**Calls:** `canopy:walkthrough` once per spec.

**Produces:** `walkthroughs/<persona>-<timestamp>/` per persona, containing the HTML slideshow, per-scene scored screenshots, and the canopy:walkthrough eval JSON.

**Re-runnability:** `/ace:step synthetic-walkthrough-run --opp X --persona <name>` re-runs a single persona without touching the others. Each invocation lands in a new timestamped folder so a project history accumulates rather than overwriting.

### 4.7 Skill 6 — `synthetic-summary`

**Reads:** all Phase 6 outputs.

**Produces:** `synthetic-summary.md` — one-page reviewer-facing summary with the public labs URL for the synthetic opp, the two workflow URLs, links to each persona slideshow, and a three-paragraph narrative summary written for someone who has never seen the system. This is the artifact a Dimagi staffer forwards to a stakeholder.

## 5. connect-labs infrastructure

### 5.1 Manifest schema

The manifest is the structured contract between `synthetic-narrative-plan` (Skill 1) and `synthetic-data-generate` (Skill 2 → engine). Pydantic-validated server-side on entry to the engine.

```yaml
opportunity_id: 1237
opportunity_name: "Demo Opportunity"
random_seed: 20260505               # determinism across re-runs

timeline:
  start_date: 2026-02-01
  end_date: 2026-04-30
  weeks: 13
  visit_cadence_per_week_per_flw: { mean: 8, stddev: 2 }

flw_personas:
  - id: "asha"
    display_name: "Asha M."
    archetype: "rockstar"           # rockstar | steady | struggling | new_hire
    accuracy_distribution: { mean: 0.92, stddev: 0.04 }
    completeness_distribution: { mean: 0.95, stddev: 0.03 }
    flag_rate: 0.02
    notes: "Senior CHW, mentors peers"
  - id: "ravi"
    archetype: "struggling"
    accuracy_distribution: { mean: 0.62, stddev: 0.10 }
    flag_rate: 0.18
    improvement_arc:
      intervention_week: 7
      post_intervention_lift: 0.15

beneficiary_cohorts:
  - id: "primary"
    size: 850
    field_distributions:
      "form.weight_kg":
        distribution: "normal"
        mean: 12.4
        stddev: 2.1
        transform: "kg"
      "form.muac_cm":
        distribution: "normal"
        mean: 13.2
        stddev: 1.4
    progression: "improvement_curve"   # improvement_curve | flat | regression

anomalies:
  - id: "weight_outlier_cluster"
    type: "field_outlier"
    field_path: "form.weight_kg"
    flw_ids: ["ravi"]
    week: 5
    detection_path: "kmc_flw_flags pipeline aggregated stage"
    reviewer_visible_in: ["llo_weekly_review week-5 snapshot"]
  - id: "missed_followups"
    type: "missing_visits"
    flw_ids: ["maria"]
    weeks: [3, 4]

kpi_config:
  - kpi: "accuracy"
    field_path: "form.weight_kg"
    aggregation: "validated_rate"
    threshold_underperform: 0.75
    threshold_target: 0.90
  - kpi: "completeness"
    field_path: "*"
    aggregation: "non_null_rate"
    threshold_underperform: 0.85

coaching_arcs:
  - flw_id: "ravi"
    week_triggered: 5
    persona: "supportive_coach"
    target_behavior: "improve weight measurement accuracy"
    transcript:                         # the full embedded OCS conversation
      - { role: "bot", text: "Hi Ravi, I noticed...", ts: "2026-03-10T09:00:00Z" }
      - { role: "flw", text: "Yes, on Tuesday...", ts: "2026-03-10T09:02:00Z" }
      # ... full coaching arc
    follow_up_outcome_week: 7
```

### 5.2 Synthetic generator engine

New package at `connect_labs/labs/synthetic/generator/`:

```
generator/
  __init__.py
  manifest.py           # Pydantic schema for the manifest above
  schema_loader.py      # Resolves form schema via existing CommCare HQ API client
  timeline.py           # Expands timeline + cadence into per-FLW visit dates
  fields.py             # Fills form_json paths from cohort distributions; injects anomalies on schedule
  status.py             # Distributes visit status / flag / review_status per Connect verification rules
  works.py              # Mints completed_works.json + completed_module.json from visits + payment units
  user_data.py          # Mints user_data.json (FLW roster) from manifest personas
  opportunity.py        # Builds opportunity.json from the live opp detail
  engine.py             # Orchestrator: manifest + schema + opp config -> 5 fixture dicts
  uploader.py           # Wraps existing gdrive.py to push the 5 files + register/update SyntheticOpportunity
  tests/                # Unit tests per module + golden-manifest integration test
```

Public entry: `engine.generate(manifest: Manifest, opportunity_detail: dict, form_schema: dict) -> dict[str, list | dict]` returning the five fixture dicts. Fully deterministic given `manifest.random_seed`. Pure Python — no Django ORM dependency until uploader composes with `gdrive.py` and the `SyntheticOpportunity` model.

### 5.3 Two new SEED workflow templates

Both ship as repo code at `connect_labs/workflow/templates/`. They are **scaffolds** — stable data plumbing the polish skill extends per-opp.

**`llo_weekly_review.py`** — single-opp, supports saved-runs.
- DEFINITION accepts `kpi_config` (list of KPI dicts: `{name, field_path, aggregation, threshold_underperform, threshold_target}`) and `coaching_task_template` (`{subject_template, ocs_persona}`).
- PIPELINE_SCHEMAS aggregates per-FLW KPIs from `user_visits`.
- RENDER_CODE shows a per-FLW row table with KPI columns, an "underperforming" filter, a "Spawn coaching task" button (creates a labs Task with embedded OCS conversation), and a chat-styled drawer that renders embedded conversations.
- Saved-runs hook captures the snapshot state per week.

**`program_admin_audit.py`** — multi-opp-capable, supports saved-runs.
- DEFINITION accepts `watched_workflow_id`.
- Reads the watched workflow's saved runs and renders week-over-week LLO process compliance.
- Columns: did the LLO save a snapshot this week, did they create coaching tasks for all underperformers, did flagged FLWs improve, completion rate of coaching conversations.

Both templates are unit-tested following existing template conventions and registered via the auto-discovery registry.

### 5.4 New MCP tools

Five new tools in `connect_labs/mcp/tools/`, registered in `tool_registry.py`. All auth via existing PAT.

| Tool | Wraps |
|------|-------|
| `synthetic_generate_from_manifest(opportunity_id, manifest_yaml)` | Calls `generator.engine.generate` + `uploader.upload_and_register`; returns `{folder_id, record_counts}` |
| `synthetic_register(opportunity_id, gdrive_folder_id, enabled)` | Direct `SyntheticOpportunity.objects.update_or_create` (also called implicitly by `synthetic_generate_from_manifest`; exposed for manual repair) |
| `synthetic_disable(opportunity_id)` | Sets `enabled=False`. Folder retained for forensics. |
| `task_create_synthetic(opportunity_id, assigned_to, subject, ocs_conversation)` | Creates a labs Task LabsRecord with `data.ocs_conversation = [{role, text, ts}, ...]` |
| `workflow_save_snapshot(workflow_id, snapshot_name, captured_at)` | Calls existing `build_snapshot` hook + persists to definition's `saved_runs[]` |

Tests: standard MCP tool tests + integration test against a small sample manifest.

## 6. ACE plugin additions

```
agents/
  synthetic-data-and-workflows.md           # Phase 6 subagent
skills/
  synthetic-narrative-plan/SKILL.md
  synthetic-narrative-plan-eval/SKILL.md
  synthetic-data-generate/SKILL.md
  synthetic-data-generate-eval/SKILL.md
  synthetic-workflow-seed/SKILL.md
  synthetic-workflow-seed-eval/SKILL.md
  synthetic-workflow-polish/SKILL.md
  synthetic-workflow-polish-eval/SKILL.md
  synthetic-walkthrough-spec/SKILL.md
  synthetic-walkthrough-spec-eval/SKILL.md
  synthetic-walkthrough-run/SKILL.md
  synthetic-summary/SKILL.md
personas/
  prospective-llo.md
  funder.md
```

Persona files are markdown, ~half a page each: priorities, language, which dashboards matter, what's a turn-off. The phase reads the canned set then overlays anything in `ACE/<opp>/personas/*.md`.

### 6.1 Updates to existing files

- `agents/ace-orchestrator.md` — insert Phase 6 in the workflow narrative; add `phases.synthetic-data-and-workflows` section to the run_state schema; document re-run model; document the absence of a Phase 6 gate.
- `lib/artifact-manifest-roles.ts` `PHASE_FOLDERS` — add `'synthetic': '6-synthetic'`, renumber 7/8/9.
- `lib/artifact-manifest.ts` — entries for each Phase 6 artifact.
- All existing skills/agents that reference old phase numbers 6/7/8 → renumber to 7/8/9. Affected: solicitation-management agent + skills, execution-manager agent + skills, closeout agent + skills.
- `agents/closeout.md` and `skills/cycle-grade/` — extend roll-up scope to include Phase 6.
- `skills/opp-eval/` — include Phase 6 evals in the umbrella scorecard.

### 6.2 run_state.yaml additions

```yaml
phases:
  synthetic-data-and-workflows:
    synthetic-narrative-plan: pending
    synthetic-data-generate: pending
    synthetic-workflow-seed: pending
    synthetic-workflow-polish: pending
    synthetic-walkthrough-spec: pending
    synthetic-walkthrough-run: pending
    synthetic-summary: pending
```

No new entries to `gates:` (Phase 6 has no irreversible action).

### 6.3 opp.yaml additions

```yaml
synthetic:
  enabled: true
  current_folder_id: <gdrive_folder_id>
  current_run_id: <run-id>
  registered_at: <ISO>
  workflows:
    llo_weekly_review_id: <int>
    program_admin_audit_id: <int>
  walkthroughs:
    - persona: prospective-llo
      slideshow_url: <relative path to slideshow.html in the run folder>
      eval_score: 8.4
      run_at: <ISO>
    - persona: funder
      slideshow_url: <...>
      eval_score: 8.7
      run_at: <ISO>
```

Walkthrough re-runs append to the `walkthroughs` list (don't overwrite).

## 7. Run folder layout

```
6-synthetic/
  synthetic-narrative-plan.md
  synthetic-narrative-plan.yaml
  synthetic-narrative-plan-eval_verdict.yaml
  synthetic-narrative-plan-eval_report.md
  synthetic-data-generate.md
  synthetic-data-generate-eval_verdict.yaml
  synthetic-data-generate-eval_report.md
  fixtures/
    opportunity.json
    user_visits.json
    user_data.json
    completed_works.json
    completed_module.json
  synthetic-workflow-seed.md
  synthetic-workflow-seed-eval_verdict.yaml
  synthetic-workflow-seed-eval_report.md
  synthetic-workflow-polish.md
  synthetic-workflow-polish-eval_verdict.yaml
  synthetic-workflow-polish-eval_report.md
  synthetic-walkthrough-spec_prospective-llo.yaml
  synthetic-walkthrough-spec_funder.yaml
  synthetic-walkthrough-spec-eval_verdict.yaml
  synthetic-walkthrough-spec-eval_report.md
  walkthroughs/
    prospective-llo-20260505-1430/
      slideshow.html
      scenes/
      eval.json
    funder-20260505-1432/
      slideshow.html
      scenes/
      eval.json
  synthetic-summary.md
```

## 8. Re-runnability

- **Linear run.** Phase 6 fires once during `/ace:run`, like any other phase. State tracked in `run_state.yaml.phases.synthetic-data-and-workflows`.
- **Regenerate data.** `/ace:step synthetic-data-generate --opp X` mints a fresh manifest (or reuses the latest), generates a new GDrive folder, flips `SyntheticOpportunity` to point at it. Old folder retained. Workflow seed + polish + walkthroughs do not re-run automatically (they consume the live synthetic data via labs, so they pick up the new fixtures on next render).
- **Refresh polish only.** `/ace:step synthetic-workflow-polish --opp X` re-edits the render code without touching data. Useful when the report quality didn't land.
- **New persona walkthrough.** `/ace:step synthetic-walkthrough-run --opp X --persona <name>` runs a single persona. New slideshow lands in a timestamped folder; `opp.yaml.synthetic.walkthroughs` list grows.
- **Full disable.** `synthetic_disable(opp_id)` flips `SyntheticOpportunity.enabled=False`. Real export data resumes flowing through labs (which is empty for this opp by design — production data is in production Connect).

## 9. Failure modes

| Failure | Detection | Recovery |
|---------|-----------|----------|
| HQ form schema unreachable | `synthetic-narrative-plan` traps `get_form_json_paths` failure | Degrade to G2 mode (PDD-only). Eval flags reduced fidelity. |
| Manifest fails Pydantic validation | `synthetic_generate_from_manifest` rejects | Skill 1 re-prompted with the validation error. Standard ACE retry pattern. |
| GDrive upload partial / 0-byte | Existing `dump.py` post-upload size verification, ported to uploader | Engine retries up to 3x then halts; skill outputs error verdict. |
| `task_create_synthetic` fails for one FLW | Per-call try/except in seed skill | Other tasks proceed; failed task IDs recorded in seed output; eval downgrades but does not fail the phase. |
| Polish skill produces broken JSX | Eval includes a syntax check + smoke render via `pipeline_preview`-style probe | Falls back to scaffold render code unchanged. Polish output records the failure. |
| `canopy:walkthrough` browser flake | canopy:walkthrough already retries internally | Non-recoverable failures surface in `synthetic-summary.md` ("funder slideshow capture failed; rerun with `/ace:step synthetic-walkthrough-run --persona funder`"). Phase still marks done. |
| Scaffold genuinely doesn't fit (rare) | Seed skill's suitability check sets `scaffold_unsuitable: true` | Polish skill rewrites render code from scratch (L2 mode). |

## 10. Evals and gating

- **No phase gate.** Phase 6 has no irreversible external action; the orchestrator does not pause at a Phase 6 boundary.
- **Per-skill auto-dispatched evals** for skills 1, 2, 3, 3.5, and 4. Standard `--no-evals` opt-out.
- **Polish-eval is the strictest** — it's the determinant of whether the demo is "amazing"-quality. Vision-model judging on rendered screenshots, plus narrative-data coherence checks.
- **Roll-up.** All Phase 6 evals join `opp-eval` (the umbrella scorecard) and `cycle-grade` (closeout) per existing convention.
- **Calibration.** Phase 6 evals plug into the existing `ace:eval-calibration` system — ground-truth catalogue per opp, multi-run variance protocol, detection-rate metric. Polish-eval's vision-model component requires extending the calibration harness; treated as part of implementation work.

## 11. Testing strategy

**connect-labs:**
- Unit tests per generator module (`manifest`, `timeline`, `fields`, `status`, `works`, `user_data`, `opportunity`).
- Golden-manifest integration test: a sample manifest produces a deterministic fixture set; bytes-equal across runs given the same `random_seed`.
- Template tests for `llo_weekly_review` and `program_admin_audit` following existing template conventions.
- MCP tool tests (request/response shape) + one end-to-end test that mints data through `synthetic_generate_from_manifest` and asserts `SyntheticOpportunity` state.

**ACE:**
- Skill-level smoke tests: manifest schema validates, generated walkthrough YAML parses, persona files lint clean (required sections present).
- Opp-level dry-run via existing ACE test harness — Phase 6 against a fixture PDD + fixture app summaries.
- Snapshot tests on the run folder layout (file presence, naming).

**Persona files:**
- Lint check enforces required sections (priorities, language, dashboards-of-interest, turn-offs). Run as part of ACE plugin CI.

## 12. Open questions for implementation

These are deferred to the implementation plan rather than answered in this design:

1. **Persona vision-judge calibration.** Polish-eval needs vision-model rubrics calibrated against representative "good" / "bad" dashboard screenshots. Bootstrap source: Phase 5's `app-screenshot-capture` outputs from prior opps + manual labeling.
2. **Concrete coaching transcript style.** The manifest describes the conversation's arc; the LLM in `synthetic-narrative-plan` writes the actual chat messages. Style guide for tone (supportive, direct, motivational?) and length (3 turns? 8 turns?) should crystallize as we generate the first few real ones.
3. **Multi-opp variant of Workflow B.** v1 wires `program_admin_audit` to a single `llo_weekly_review`. Bundling sibling demo opps into one admin view is straightforward via the existing multi-opp pattern, but is left for a follow-up once we have multiple Phase-6'd opps.
4. **Phase number references in third-party docs.** Any external docs (Notion, internal wikis) that reference Phase 6/7/8 by number will go stale on rollout. Out of scope for this PR; tracked separately.

## 13. Rollout

Suggested ordering for implementation PRs:

1. **PR 1 (connect-labs):** Synthetic generator engine + tests. No MCP exposure yet; verifies engine in isolation.
2. **PR 2 (connect-labs):** Two new SEED templates + tests. Independent of engine; verifiable via existing template auto-discovery.
3. **PR 3 (connect-labs):** Five new MCP tools wiring engine + templates + task creation + snapshot save. Manual sanity test against a dev opp.
4. **PR 4 (ACE plugin):** Phase 6 agent + 7 skills + 4 evals + persona catalog. Renumbering of phases 7/8/9.
5. **PR 5 (ACE plugin):** Eval calibration extensions (vision-judge bootstrap data, polish-eval rubric).

PR 4 depends on PR 3 being deployed to labs prod. PRs 1, 2, 3 can land without PR 4 — labs gets a synthetic generator usable manually before ACE drives it.
