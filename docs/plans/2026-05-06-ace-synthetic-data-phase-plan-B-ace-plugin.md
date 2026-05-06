# ACE Phase 6 — Plan B: ACE Plugin Side

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the ACE plugin side of Phase 6 (Synthetic Data and Workflows) in four shippable stages, starting with a 3-skill MVP that proves end-to-end synthetic generation against a real ACE-built opp, then iteratively layering on walkthrough generation, workflow seeding/polish, and the full Phase 6 infrastructure (agent + evals + renumbering).

**Architecture:** ACE's per-skill model — each `skills/<name>/SKILL.md` is a markdown procedure the orchestrator dispatches. Each skill reads opp inputs, calls MCP tools (mostly the connect-labs MCP we shipped in Plan A + Plan A+), writes artifacts to `ACE/<opp>/runs/<run-id>/6-synthetic/`, and updates `opp.yaml` / `run_state.yaml`.

**Tech stack:** Markdown skills consumed by Claude Code's Skill tool, the deployed connect-labs MCP at `https://labs.connect.dimagi.com/mcp/` (`synthetic_*` tools live), the `canopy:walkthrough` skill (already exists), and the `email-communicator` / `gdrive` MCPs for outputs.

**Companion docs:**
- Design: [`2026-05-05-ace-synthetic-data-phase-design.md`](./2026-05-05-ace-synthetic-data-phase-design.md)
- Plan A (connect-labs side, shipped): [`2026-05-05-ace-synthetic-data-phase-plan-A-connect-labs.md`](./2026-05-05-ace-synthetic-data-phase-plan-A-connect-labs.md)

**Where the work lands:** `~/emdash/repositories/ace/` (GitHub `jjackson/ace`, currently at v0.13.44). Skills go under `skills/<name>/SKILL.md`, agent under `agents/synthetic-data-and-workflows.md`, manifest entries in `lib/artifact-manifest.ts` and `lib/artifact-manifest-roles.ts`.

---

## Stage 1 — Minimum viable demo (3 skills)

End state: a human can run `/ace:step synthetic-data-generate --opp turmeric` and see real synthetic FLW + visit + payment data in labs.connect.dimagi.com for the turmeric opp, with the opp's actual deliver-form question paths in `form_json`. No agent, no evals, no walkthrough — just the data plumbing.

This stage proves the connect-labs MCP we deployed actually works against an ACE-built opp.

### Stage 1 prerequisites — UUID→int translation strategy

For this MVP, the operator manually provides the integer opp_id when invoking the skill. The skill takes both the UUID (from `opp.yaml.connect.opportunity.id`) and an `--opp-int-id` arg. A future stage automates the lookup.

The future automation has three options, listed in increasing surface-area:
- **(A)** Have ACE's `connect-opp-setup` skill record the integer ID in `opp.yaml` at creation time. Requires touching the existing skill (small change). The integer would need a lookup somewhere — easiest source is calling `connect_list_opportunities(organization_slug)` on the ACE-connect MCP, then filtering by name match against the just-created opp.
- **(B)** Have `synthetic-data-generate` do the lookup itself by calling `connect_list_opportunities(org_slug)` + name match.
- **(C)** Add UUID exposure to `/export/opp_org_program_list/` upstream in `dimagi/commcare-connect`. Nicest long-term; out of scope here.

Stage 1 punts on this entirely (operator types the integer). Stage 4 picks one of A/B.

### Task 1.1: Create `synthetic-data-generate` skill

**Files:**
- Create: `~/emdash/repositories/ace/skills/synthetic-data-generate/SKILL.md`

**Inputs to the skill:**
- `--opp <slug>` — required, e.g. `turmeric`
- `--opp-int-id <integer>` — required for v1, the labs-side integer opportunity ID
- `--manifest <path>` — optional path to a manifest YAML the operator pre-authored. If omitted, the skill writes a default manifest to the run folder and pauses for operator review/edit.

**What the skill does (procedure):**

- [ ] **Step 1: Resolve the opp identity**

  Read `ACE/<opp>/opp.yaml` from Drive via `mcp__plugin_ace_ace-gdrive__drive_read_file`. Parse YAML. Extract:
  - `connect.program.id` (UUID)
  - `connect.opportunity.id` (UUID)
  - `connect.opportunity.url`
  - `last_run_id`

  Construct the run folder path: `ACE/<opp>/runs/<run-id>/6-synthetic/`. Create it if missing via `drive_create_folder`.

- [ ] **Step 2: Author or load the manifest**

  If `--manifest` is supplied, read the file. Otherwise generate a default by reading:
  - The PDD at `ACE/<opp>/inputs/pdd.md`
  - The connect-setup-summary at `ACE/<opp>/connect-setup-summary.md` (for payment units, deliver units)

  The default manifest:
  - 5 FLW personas (1 rockstar, 2 steady, 1 struggling, 1 new_hire) with archetype-typical stat distributions
  - 1 beneficiary cohort sized 50, with field distributions only for fields the operator can fill in by hand
  - 4-week timeline starting 30 days ago, 8 visits/week/FLW
  - 1 KPI from `kpi_config` based on a primary measurement field guessed from the PDD
  - Empty `anomalies`, empty `coaching_arcs`

  Save the manifest as `6-synthetic/synthetic-data-generate_manifest.yaml`.

  **Pause for operator review unless `--no-pause` is set.** The default manifest is a starting point; operator typically tunes FLW count, cohort size, timeline, and adds 1–2 anomalies before generating.

- [ ] **Step 3: Call the MCP tool**

  Call the labs MCP via Claude Code's MCP runtime:
  ```json
  {
    "tool": "synthetic_generate_from_manifest",
    "arguments": {
      "opportunity_id": <integer from --opp-int-id>,
      "manifest_yaml": "<full manifest text>"
    }
  }
  ```

  On `PERMISSION_DENIED` → halt with a clear message: "user is not in the labs accessible_opp_ids set for opp_id=<int>; check Connect membership."
  On `INVALID_SCHEMA` → write the error to a `synthetic-data-generate_error.md` and halt.
  On success, capture `folder_id`, `record_counts`, `form_schema_questions`.

- [ ] **Step 4: Write the run summary**

  Write `6-synthetic/synthetic-data-generate.md` with:
  - Manifest filepath in Drive
  - GDrive folder ID (the synthetic fixture folder, with a clickable URL)
  - Record counts per endpoint
  - Form schema question count
  - The labs URL where the synthetic data is now visible: `https://labs.connect.dimagi.com/<opp-views>` (consult opp.yaml for the right path)
  - Any warnings from the MCP response (e.g., "form_schema_questions=0 — deliver app empty or unreachable")

- [ ] **Step 5: Update opp.yaml and run_state.yaml**

  Add to `opp.yaml`:
  ```yaml
  synthetic:
    enabled: true
    current_folder_id: <folder_id>
    current_run_id: <run-id>
    generated_at: <ISO>
    fixture_record_counts: { user_visits: N, user_data: N, ... }
  ```

  Add to `run_state.yaml`:
  ```yaml
  phases:
    synthetic-data-and-workflows:
      synthetic-data-generate: done
  ```

  Use `mcp__plugin_ace_ace-gdrive__update_yaml_file` for surgical edits.

**Skill-MD shape:** Follow `skills/idea-to-pdd/SKILL.md` as the structural reference. Top frontmatter (`name`, `description`), `# Display Name`, `## Process`, `## MCP Tools Used` (list `synthetic_generate_from_manifest`, `drive_read_file`, `drive_create_folder`, `update_yaml_file`), `## Mode Behavior` (auto-pause behavior re: manifest review), `## Failure Modes`, `## Change Log`.

**Acceptance:** running `/ace:step synthetic-data-generate --opp turmeric --opp-int-id <N>` against a real opp produces a registered synthetic opp visible in labs and a populated run-folder summary.

### Task 1.2: Create `synthetic-summary` skill

**Files:**
- Create: `~/emdash/repositories/ace/skills/synthetic-summary/SKILL.md`

**Procedure:**

- [ ] **Step 1: Read all Phase 6 artifacts.**

  From the run folder `ACE/<opp>/runs/<run-id>/6-synthetic/`:
  - `synthetic-data-generate.md` (always present after Task 1.1)
  - `synthetic-data-generate_manifest.yaml`

  In Stage 1 these are the only inputs. Later stages add more.

- [ ] **Step 2: Compose `6-synthetic/synthetic-summary.md`.**

  One-page reviewer-facing markdown:
  - Headline: opp name + slug + a single sentence of context
  - The labs URL where the demo lives (clickable)
  - The GDrive folder where the fixtures live
  - Three-paragraph narrative summary describing what a stakeholder will see when they click through (drawn from the manifest's FLW personas, anomalies, and KPI thresholds)
  - "What's next" — pointer to Plan B's later stages (workflow seeding, walkthrough decks) when those land

  This is the artifact a Dimagi staffer forwards to a stakeholder.

- [ ] **Step 3: Update run_state.yaml** with `synthetic-summary: done`.

### Task 1.3: Manual smoke (turmeric)

**Files:** none

- [ ] **Step 1**: Find turmeric's labs-side integer opportunity ID. Easiest path: open https://labs.connect.dimagi.com/labs/synthetic/ in the browser (logged in as ace@dimagi-ai.com), find the opp in the dropdown, capture the integer.

- [ ] **Step 2**: Run `/ace:step synthetic-data-generate --opp turmeric --opp-int-id <N>`. Operator may pause to tune the manifest before generation.

- [ ] **Step 3**: Verify the GDrive folder has 5 JSON files and the labs UI for the turmeric opp shows the synthetic FLWs and visits.

- [ ] **Step 4**: Run `/ace:step synthetic-summary --opp turmeric`. Read the summary file; confirm it's worth forwarding to a stakeholder.

- [ ] **Step 5**: Tear down via direct `synthetic_disable` call (no skill yet). Confirm labs reverts.

### Stage 1 PR

Title: `feat(synthetic): MVP synthetic-data-generate + synthetic-summary skills`. Single PR, two skills, no agent or eval changes. Ships when turmeric smoke passes.

---

## Stage 2 — Walkthrough decks (3 more skills)

End state: per-persona stakeholder-ready slideshows that bundle with the synthetic-summary output. Demo can be forwarded to a prospective LLO or a funder.

### Task 2.1: `synthetic-narrative-plan` skill

**Files:**
- Create: `~/emdash/repositories/ace/skills/synthetic-narrative-plan/SKILL.md`

**What it does:**
- Reads the same inputs as `synthetic-data-generate` (PDD, app summaries, expected-journeys.md, app-test-cases.yaml, Connect setup summary).
- LLM-authors a *richer* manifest than the v1 default — specific FLW personas with names + notes, deliberate anomaly events ("Asha's Tuesday weight outliers in week 5"), a coaching arc transcript per anomaly, and a deliberate week-over-week story.
- Writes `synthetic-narrative-plan.md` (human-readable narrative) and `synthetic-narrative-plan.yaml` (the manifest the LLM produced).
- Updates `synthetic-data-generate` to consume `synthetic-narrative-plan.yaml` as its default manifest source (so authors don't have to pass `--manifest` explicitly when the plan skill ran first).

**Position in flow:** runs *before* `synthetic-data-generate` from now on. The skill chain becomes: narrative-plan → data-generate → summary (Stage 1's order is preserved by making narrative-plan optional).

### Task 2.2: `synthetic-walkthrough-spec` skill

**Files:**
- Create: `~/emdash/repositories/ace/skills/synthetic-walkthrough-spec/SKILL.md`
- Create: `~/emdash/repositories/ace/personas/prospective-llo.md`
- Create: `~/emdash/repositories/ace/personas/funder.md`

Each persona is ~half a page: priorities, language, dashboards-of-interest, turn-offs.

**Procedure for the skill:**
- Reads narrative plan + the persona catalog (canned + opp-specific overlays from `ACE/<opp>/personas/*.md`).
- For each persona, generates `6-synthetic/synthetic-walkthrough-spec_<persona>.yaml` — ordered scenes (URL, click sequence, caption, "wow moment" assertion). Wow moments correspond to seeded anomalies from the narrative plan.

### Task 2.3: `synthetic-walkthrough-run` skill

**Files:**
- Create: `~/emdash/repositories/ace/skills/synthetic-walkthrough-run/SKILL.md`

**Procedure:**
- For each persona spec, dispatch `canopy:walkthrough` with the spec.
- `canopy:walkthrough` produces an HTML slideshow + scored screenshots; the skill copies them to `6-synthetic/walkthroughs/<persona>-<timestamp>/`.
- Updates `opp.yaml.synthetic.walkthroughs[]` (a list, not a map — re-runs append) with persona name, slideshow path, eval score, run timestamp.

### Task 2.4: Update `synthetic-summary` to bundle walkthroughs

Extend `synthetic-summary.md` to include links to each persona slideshow when they exist. (Stage 1's summary still works when walkthroughs are absent.)

### Stage 2 PR

Title: `feat(synthetic): persona walkthroughs for ACE Phase 6`. Three new skills + persona catalog + summary update.

---

## Stage 3 — Workflow seeding + polish (2 more skills)

End state: the two SEED templates from Plan A (`llo_weekly_review`, `program_admin_audit`) get instantiated against the synthetic opp with KPI config wired up, and per-opp polish edits make the dashboards look genuinely tailored. Saved-runs snapshots show week-over-week progression.

### Task 3.1: `synthetic-workflow-seed` skill

**Files:**
- Create: `~/emdash/repositories/ace/skills/synthetic-workflow-seed/SKILL.md`

**Procedure:**
- Read the manifest's `kpi_config` and `coaching_arcs`.
- Call connect-labs MCP `workflow_create_from_template('llo_weekly_review', config={kpi_config, coaching_task_template})`.
- Run pipelines once (existing labs job — TBD how skill triggers this; may need a new MCP tool `workflow_run_pipelines` or use the existing pipeline_preview).
- For each FLW marked `archetype: struggling` or with a triggered coaching arc, call `task_create_synthetic` with the arc's transcript embedded in `data.ocs_conversation`.
- Call `workflow_save_snapshot(run_id, "Week 1", week_1_end_iso)` — note this requires the workflow to have a *run* (not just a definition), so the skill must first create a run via the workflow's existing API.
- Repeat the data update + task creation + snapshot save for Week 2.
- Call `workflow_create_from_template('program_admin_audit', config={watched_workflow_id})`.

**Note on `workflow_save_snapshot`'s run-ID requirement (from Plan A's C1 fix):** the tool takes `run_id`, not `workflow_id`. The seed skill must create a workflow run first. Look at how the existing labs UI completes a run (there's a `complete_run` endpoint and a "Save snapshot" button in the runner). If a programmatic API doesn't exist, this is a small connect-labs follow-up: add `workflow_create_run(definition_id, opportunity_id)` MCP tool, or extend an existing tool.

### Task 3.2: `synthetic-workflow-polish` skill

**Files:**
- Create: `~/emdash/repositories/ace/skills/synthetic-workflow-polish/SKILL.md`

**Procedure:**
- Read scaffold render code via `workflow_get`.
- Read narrative plan + fixture summaries (specific FLW names, numbers to feature).
- Call `workflow_patch_render_code` for surgical edits (hero panel, FLW story cards, anomaly callouts).
- Falls back to `workflow_update_render_code` (full rewrite) if seed flagged `scaffold_unsuitable: true`.
- Internally can use the existing `workflow-author` skill in connect-labs to iterate on render code via the MCP.

### Task 3.3: Connect-labs follow-up (only if needed) — `workflow_create_run` MCP tool

This is a connect-labs PR, not an ACE PR. Surfaced here because Stage 3 may discover the gap.

**Trigger:** if no programmatic way to create a workflow run exists in the deployed MCP, add a new tool that wraps `WorkflowDataAccess.create_run(definition_id, opportunity_id)`. Mirrors the pattern from `workflow_save_snapshot`. Single tool, ~50 lines + tests.

**Skip if:** the existing `workflow_create_from_template` already returns a runnable instance, or another existing tool fills this gap.

### Stage 3 PR

Title: `feat(synthetic): workflow seeding + polish for ACE Phase 6`. Two new ACE skills + (optional) one connect-labs follow-up PR.

---

## Stage 4 — Full Phase 6 infrastructure (agent + evals + renumbering)

End state: `/ace:run <opp>` automatically runs Phase 6 in sequence. Per-skill evals score each artifact. Phases 7/8/9 (currently 6/7/8) are renumbered consistently.

### Task 4.1: Phase 6 agent

**Files:**
- Create: `~/emdash/repositories/ace/agents/synthetic-data-and-workflows.md`

Mirrors the structure of `agents/qa-and-training.md` (a similarly-shaped phase that runs many skills sequentially). Frontmatter `phase_ordinal: 6`, `phase_display: "Synthetic Data and Workflows"`, lists all 7 skills + 4 evals. Workflow section walks through each skill in dispatch order with brief context.

### Task 4.2: Four eval skills

For each producer skill that ships an artifact requiring judgment, create a paired `-eval` skill:
- `synthetic-narrative-plan-eval`
- `synthetic-data-generate-eval`
- `synthetic-workflow-seed-eval`
- `synthetic-workflow-polish-eval`
- `synthetic-walkthrough-spec-eval`

(The spec deferred 4 evals; the design called for 5. Pick whichever number actually pans out — if `synthetic-walkthrough-run` is graded by `canopy:walkthrough` already, no separate eval; same for `synthetic-summary` since it's a pure aggregator.)

Each follows `skills/_eval-template.md`. Outputs a verdict YAML per the canonical shape (see `skills/README.md` Verdict YAML section).

### Task 4.3: Phase renumbering

Mechanical but touches many files. Affected:
- `lib/artifact-manifest-roles.ts` — add `'synthetic': '6-synthetic'`, shift `solicitation-management → 7`, `execution-manager → 8`, `closeout → 9`.
- `lib/artifact-manifest.ts` — entries for each Phase 6 artifact.
- `agents/ace-orchestrator.md` — insert Phase 6 in the workflow narrative; document re-run model; update phase ordinal references in mode behavior + state schema.
- All existing phase agents (solicitation-management, execution-manager, closeout) and their skills — update phase number references in frontmatter and prose.

This is the largest single change in Plan B. Ship as its own PR after Stages 1–3 are settled to minimize merge churn.

### Task 4.4: Update opp.yaml + run_state.yaml schemas

Add `phases.synthetic-data-and-workflows` to the run_state schema with all 7 skills + 4 evals listed.
Add `synthetic` block schema to opp.yaml (already started in Stage 1).

### Task 4.5: UUID→int automation

Pick option A or B from Stage 1 prerequisites and implement. My recommendation is **A** (have ACE's `connect-opp-setup` skill record the integer in `opp.yaml`). Implementation:
- Add a step at the end of `connect-opp-setup`: after `connect_create_opportunity` returns the UUID, call `connect_list_opportunities(organization_slug)` and find the matching opp by name. Record the integer in `opp.yaml.connect.opportunity.int_id`.
- Update `synthetic-data-generate` to read `connect.opportunity.int_id` from opp.yaml as the default for `--opp-int-id`.

### Stage 4 PR(s)

Title: `feat(synthetic): Phase 6 agent + evals + renumbering`. Likely two PRs: agent+evals first, renumbering second. Renumbering touches many files but is mechanical.

---

## Cross-stage decisions

### Re-runnability

- **Linear `/ace:run`:** Stage 4 wires Phase 6 into the orchestrator's sequence.
- **Single-skill re-run:** `/ace:step <skill-name> --opp <slug>` — works at every stage. Each skill checks `run_state.yaml.phases.synthetic-data-and-workflows.<skill>` and either skips (already done) or proceeds.
- **Persona walkthrough re-run:** `/ace:step synthetic-walkthrough-run --opp <slug> --persona <name>` produces a new timestamped slideshow. The opp.yaml `walkthroughs[]` list grows.
- **Full disable:** direct call to `synthetic_disable(int_id)` (no skill yet — call it manually for v1).

### What's NOT in Plan B

- **No solicitation handoff.** Phase 7 (solicitation-management) work is happening separately; Plan B doesn't create the linkage.
- **No automated eval calibration.** The eval skills land with a default rubric in Stage 4; calibration tuning is a follow-up.
- **No vision-model judging.** `synthetic-workflow-polish-eval` would ideally judge rendered screenshots. Stage 4 lands the eval skill with text-based rubrics; vision judging is a follow-up.

## Recommended execution order

If I were starting now:

1. **Stage 1, today** — narrowly scoped, validates the deployed connect-labs MCP works against a real ACE opp. Two skills, ~600 lines of markdown total. Smoke against turmeric.
2. **Stage 2 next** — walkthroughs are the most stakeholder-visible payoff. Three skills + persona catalog. Reuses `canopy:walkthrough`.
3. **Stage 3 after** — workflow seeding is more complex (saved-runs lifecycle, possible connect-labs follow-up). Worth landing once Stages 1–2 are battle-tested.
4. **Stage 4 last** — phase renumbering is mechanical churn; doing it last avoids re-doing it whenever stages 1–3 evolve.

After each stage merges to the ACE plugin's main, bump the plugin version (auto-handled by the existing release process per the commit log — `0.13.X → 0.14.0` for Stage 1, etc.).
