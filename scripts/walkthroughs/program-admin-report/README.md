# Program Admin Report — synthetic walkthrough scripts

This folder versions the cross-opportunity Program Admin Report (PAR)
demo: synthetic-data config, the recorder scripts that drive the live
videos, and the HTML deck capture script.

Everything generic — Playwright primitives, cursor overlay, PAR snapshot
walker, ffmpeg concatenation, pre-record verify checks — lives in
[`scripts/walkthroughs/_lib/`](../_lib). The per-walkthrough files here
should look like scene sequences, not a re-implementation of the
recording framework.

## What it produces

1. A **synthetic data set** on labs prod (`labs.connect.dimagi.com`):
   2 opps with 4 COMPLETED weekly chc_nutrition runs each (the PAR
   window — computed dynamically as the trailing 4 complete Mondays, so
   the demo is always current-dated), real audits + tasks per FLW
   archetype, and a cross-opp Program Admin Report run that watches
   them. Northern completes all 4 weeks (aggregate reads **SOP MET**);
   Southern misses one and carries the open work (reads **BELOW**).
   COMPLETED runs get seeded Flag records mirroring what the chc render's
   `view.ensureAutoFlags` would have created (it only fires on live runs,
   so without seeding the PAR drill panels showed FLAGS "—" beside tasks
   asserting the rule). Northern additionally gets an `in_progress`
   run for the CURRENT week — deliberately OUTSIDE the PAR window, so
   the manager-flow video can show a real "do the review live"
   sequence without the grid rendering a NO RUN hole for that week.
2. A **manager-flow video** (`manager_flow.mp4`, ~40s) — the network
   manager arriving at the in_progress current-week review, the auto-flags
   appearing on mount, auditing the one flagged FLW (jumoke_n) live
   via the `Create Audit ▾` menu's "Audit Last 7 days" item,
   navigating to the resulting task via the `Create Task ▾` menu's
   "Coach on Flag implications" item (only shown when the row carries
   any flag), and firing the "Initiate AI Assistant" coaching chat
   with the pre-filled prompt.
3. A **drill-through video** (`drill_through.mp4`, ~80s) — the 9-scene
   tour from the completed PAR grid down into one flagged FLW's audit
   pictures and OCS coaching task.
4. A **stakeholder HTML deck** (`program-admin-report.html`) — 12 slides
   wrapping the same scenes with persona narration + score annotations.

The intended final artifact for sharing is the concatenated MP4
(manager_flow + drill_through) plus the HTML deck.

## Pipeline

`regenerate.py` is the **single synthetic-generator entrypoint** — the
command a canopy `setup:` block invokes before rendering. One run
generates the data, verifies it, gates on deploy freshness, resolves the
drill targets, and emits the FLAT vars JSON every downstream consumer
reads.

```text
demo_config.json
   │
   │  regenerate.py
   │    1. generate   — program_admin_demo_seed MCP tool (server-side on labs)
   │    2. verify     — _lib/verify smoke checks on the generation result
   │    3. freshness  — served render_code vs local checkout (abort on stale deploy)
   │    4. discover   — _lib/discovery PAR-snapshot walk → good/incomplete targets
   │    5. emit vars  — FLAT .run_ids.json (ids + path-relative URLs + FLW names)
   ▼
.run_ids.json  (the vars contract — see below)
   │
   ├─►  record_manager_flow.py  ──►  /tmp/par_preview/video_manager/*.webm
   ├─►  record_drill_through.py  ─►  /tmp/par_preview/video/*.webm
   │      │
   │      └─►  scripts/walkthroughs/_lib/concat.py  ──►  program-admin-report.mp4
   │
   └─►  capture_walkthrough.py  ──►  /tmp/walkthrough-run-data.json
            │
            └─►  canopy generate_presentation.py  ──►  program-admin-report.html
```

**DDD migration direction**: the Playwright recorders are slated to be
replaced by a canopy DDD unified spec whose `setup:` block runs
`regenerate.py` and whose scenes interpolate the vars JSON
(`goto: ${par_url}`, `click: text:Audit #${incomplete_audit_id}`, …).
Their hard-won selector + flow knowledge is preserved in
[`ACTIONS_MAP.md`](ACTIONS_MAP.md) — the recorders are deleted only after
a DDD render proves parity against that map.

## Files

| File                      | What it does                                                                                                                                                                                                                                                                                                                        |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `demo_config.json`        | The opps + FLW-archetypes + flag-weeks config passed to the synthetic generator. Edit this to change the demo narrative. The week window itself is NOT in the config — `regenerate.py` computes `completed_weeks` trailing complete Mondays (the PAR window) plus the current week (the in-progress manager run) from today's date. |
| `regenerate.py`           | The synthetic-generator entrypoint: generate (via the `program_admin_demo_seed` MCP tool) → verify → freshness gate → drill-target discovery → emit the FLAT vars JSON. Requires `LABS_MCP_TOKEN` + a labs session file.                                                                                                            |
| `.run_ids.json`           | Generated. The FLAT vars contract (see below) — raw ids, path-relative URLs, and archetype-derived FLW names. The recorders read this — never falls back to a stale hardcoded int.                                                                                                                                                  |
| `ACTIONS_MAP.md`          | Scene-by-scene map of every recorder interaction in canopy action vocabulary, with `${var}` placeholders. Source material for the future DDD unified spec.                                                                                                                                                                          |
| `record_manager_flow.py`  | Playwright recorder for the manager-flow video. Reads `.run_ids.json`; drives clicks via `_lib/recorder.py` primitives.                                                                                                                                                                                                             |
| `record_drill_through.py` | Playwright recorder for the completed-PAR drill-through. Re-walks the PAR snapshot at record time (the canonical resolution now happens in `regenerate.py`).                                                                                                                                                                        |
| `capture_walkthrough.py`  | Screenshot pass for the HTML deck. Each scene is keyed by a `target` keyword in `docs/walkthroughs/program-admin-report.yaml`; this script maps target → URL + post-load action.                                                                                                                                                    |

## Vars contract (.run_ids.json)

FLAT JSON — string/number values only — so a canopy `setup.outputs` block
can interpolate any key as `${var}`. URLs are **path-relative** (the spec
carries `base_url`). FLW usernames are archetype-derived at generation
time so the spec never hardcodes them.

| Key                                            | Meaning                                                                                                                     |
| ---------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `generated_at`                                 | ISO timestamp of the generation (staleness check in `_lib/config`).                                                         |
| `par_def_id`, `par_run_id`                     | Program Admin Report definition + completed run.                                                                            |
| `opp_id`, `workflow_def_id`                    | Primary opp (Northern) + its chc_nutrition definition.                                                                      |
| `wk4_run_id`                                   | Northern's CURRENT-week in_progress run (manager-flow target). Key name is historical; the run sits outside the PAR window. |
| `par_url`, `wk4_url`                           | Run-page paths for the two entry points.                                                                                    |
| `chc_good_url`                                 | Weekly-review path for the good drill run.                                                                                  |
| `audit_good_url`, `task_good_url`              | Good drill: completed audit + closed task pages.                                                                            |
| `audit_incomplete_url`, `task_incomplete_url`  | Incomplete drill: in-review audit + investigating task pages.                                                               |
| `good_opp_id/_label`, `good_week_idx`          | Grid-cell coordinates for the good drill cell.                                                                              |
| `good_run_id`, `good_audit_id`, `good_task_id` | Raw ids (click targets like `Task #${good_task_id}`).                                                                       |
| `incomplete_*` (same shape as `good_*`)        | Grid-cell coordinates + raw ids for the incomplete drill.                                                                   |
| `flagged_flw_manager`                          | FLW the manager audits + coaches live (e.g. `jumoke_n`).                                                                    |
| `flagged_flw_good`, `flagged_flw_incomplete`   | FLWs behind the good / incomplete drills (e.g. `hawa_n`, `ola_s`).                                                          |

## Architecture

```
scripts/walkthroughs/
├── _lib/                            ← shared scaffolding
│   ├── cursor_overlay.js              versioned synthetic cursor
│   ├── config.py                       LABS_SESSION_FILE + .run_ids.json
│   ├── recorder.py                     RecorderSession + click_text + snap
│   ├── grid.py                         PAR-grid cell clicker
│   ├── discovery.py                    PAR snapshot walker
│   ├── verify.py                       pre-record smoke checks
│   └── concat.py                       ffmpeg wrapper
└── program-admin-report/            ← per-walkthrough scenes + config
    ├── README.md
    ├── demo_config.json
    ├── regenerate.py
    ├── record_manager_flow.py
    ├── record_drill_through.py
    └── capture_walkthrough.py
```

The synthetic-data generator itself lives in
`commcare_connect/labs/synthetic/program_admin_demo.py` next to the
other synthetic infrastructure (archetypes, manager_flow_views,
gdrive corpus). The generic primitives it uses
(`monday_dt`, `cleanup_opportunity_workflows`, `create_backdated_workflow_run`,
`apply_action_spec`, …) live in
`commcare_connect/labs/synthetic/walkthrough_kit.py` — those are the
pieces a second walkthrough should import to avoid reinventing the
LabsRecord write plumbing.

## Running it end-to-end

```bash
# 1. Auth (one-time per session):
#    - a labs MCP PAT for the synthetic generator (or a configured
#      connect_labs server in ~/.claude.json):
export LABS_MCP_TOKEN=...
#    - a labs browser session for the freshness/discovery fetches and the
#      recorders (run /ace:labs-login; override path via LABS_SESSION_FILE):
# export LABS_SESSION_FILE=~/.ace/labs-session.json

# 2. Run the synthetic generator: generate + verify + freshness gate +
#    drill-target discovery + emit the FLAT vars JSON.
python scripts/walkthroughs/program-admin-report/regenerate.py

# 3. Record the manager-flow prepend.
python scripts/walkthroughs/program-admin-report/record_manager_flow.py

# 4. Record the drill-through.
python scripts/walkthroughs/program-admin-report/record_drill_through.py

# 5. Encode + concatenate to a single MP4.
python -m scripts.walkthroughs._lib.concat \
    /tmp/par_preview/video_manager/*.webm \
    /tmp/par_preview/video/*.webm \
    --out program-admin-report.mp4

# 6. Capture the HTML deck.
python scripts/walkthroughs/program-admin-report/capture_walkthrough.py
python ~/emdash-projects/canopy/scripts/walkthrough/generate_presentation.py \
    --input /tmp/walkthrough-run-data.json \
    --output program-admin-report.html
```

## Dependencies

- Python 3.11 (matches labs venv)
- Playwright (`pip install playwright && playwright install chromium`)
- ffmpeg (for video encoding + concatenation)
- Labs OAuth token in env + a session file at the path from
  `_lib/config.session_path()` (defaults to `~/.ace/labs-session.json`,
  overridable via `LABS_SESSION_FILE`).
- Canopy plugin (`~/emdash-projects/canopy`) for the HTML deck generator

## Adding a second walkthrough

This is the main reason `_lib/` exists. To add another demo:

1. Create a new sibling folder under `scripts/walkthroughs/` (e.g.
   `scripts/walkthroughs/kmc-longitudinal/`).
2. Write the per-walkthrough config (`demo_config.json` or equivalent).
3. Write a thin `regenerate.py` that calls your synthetic generator (via
   the `connect_labs` MCP — see `_lib/labs_mcp.py`) + uses
   `_lib.config.write_run_ids` to emit a FLAT vars JSON with every id and
   path-relative URL your scenes need.
4. Write `record_*.py` scripts that open a `_lib.recorder.RecorderSession`
   and walk through your scenes.
5. If your generator can share the chc_nutrition story shape, you can
   reuse most of `commcare_connect/labs/synthetic/walkthrough_kit.py`
   directly. Otherwise add new primitives to `walkthrough_kit` for
   anything generic, and keep the trajectory/orchestrator in
   `commcare_connect/labs/synthetic/<your_demo>.py`.
6. Register the MCP-callable shim in `commcare_connect/mcp/tools/<your_demo>.py`
   (~30 lines — pattern of `commcare_connect/mcp/tools/program_admin_demo.py`).

The expensive primitives (cursor overlay, PAR snapshot walker, ffmpeg
concat, FLW row scroll/click, MCP client, freshness guard) are already in
`_lib/`; the new walkthrough should be ~60 LOC of scene sequence + a few
hundred lines of demo-specific synthetic generator.

## Evolution notes (footguns to watch for)

- **In_progress runs need a snapshot fallback**: chc_nutrition's render
  code reads `instance.snapshot.pipelines` as a third fallback after
  `view.pipelines` and the top-level `pipelines` prop. Without this,
  in_progress synthetic runs show "No data available" because the live
  CSV pipeline returns nothing in the synthetic env.
- **MUAC severity is INVERTED vs intuition** (post PR #281/#287): the
  flag direction is `sam_low` (SAM < 1%) / `mam_low` (MAM < 3%) — a
  _suspiciously low_ rate means the FLW is cherry-picking easy,
  well-fed households and missing at-risk kids. So a "flagged"
  archetype's distribution has **zero** SAM/MAM mass (severity 2+ →
  truncated left tail), and a _clean_ FLW has a realistic baseline of
  SAM/MAM cases (~3-7% SAM). If you ever change the FLAG_CATALOG
  thresholds in `chc_nutrition_analysis.py`, the generator's
  `_muac_distribution` must move in lockstep — the
  `test_flagged_muac_archetypes_actually_trip_a_flag` guard in
  `test_archetypes.py` fails loudly when they drift.
- **OCS bot list short-circuit**: synthetic opps return a single canned
  "MUAC Coaching (Synthetic Demo Bot)" so the existing "Initiate AI
  Assistant" modal renders with a selectable bot — without requiring a
  real OCS account.
- **PAR snapshot doesn't include in_progress runs**: the snapshot's
  `watched_summary` only captures completed runs. The Wk4 in_progress
  run id must come from `.run_ids.json`, written by `regenerate.py`.
- **Audit + task leftovers wedge the recorder**: if a previous recorder
  run created an audit + task for the bad-MUAC FLW, the next run's
  Create Audit/Create Task menu items flip to "View Audit"/"View Task"
  (state-aware, PR #289) instead of offering a fresh create. Always
  regenerate (which cleans first) before re-recording. The verify
  checks in `regenerate.py` catch most of these post-generation.

## Operating playbook (read this before recording after a deploy)

Hard-won order of operations. Most of the pain in building this demo came
from skipping a step here.

### The golden path

1. **Land your code** (PR merged to `main`).
2. **Deploy labs** (`gh workflow run deploy-labs.yml --ref main`) and wait
   for it to report success.
3. **Wait for worker cutover, then regenerate.** The deploy "succeeding"
   does NOT mean the new code is live — ECS workers serve the OLD image for
   **2-4 more minutes** (hard-cutover task replacement + gunicorn warm-up).
   Regenerating immediately writes stale template code (see "regeneration is
   the upgrade path" below). You no longer need to poll by hand:
   `regenerate.py` runs a **freshness preflight** itself — after generating,
   it fetches the run pages and compares the served `render_code` (from the
   `#workflow-data` json_script) to your local checkout's template
   (AST-extracted, `_lib/freshness.py`) and **aborts loudly on mismatch**
   with a wait-and-retry message. `SKIP_FRESHNESS=1` bypasses the gate —
   dangerous; you'd be staging a demo on UI that doesn't match the code you
   think is live.
4. **Record.** Both recorders re-run the same freshness check
   (`assert_page_current`) right after loading their first run page, as a
   belt-and-braces guard for the time gap between generation and recording.
5. **Concat + upload** (see "Running it end-to-end").

### Why regeneration is the upgrade path

Regeneration does NOT delete workflow definitions (only runs/flags/tasks/
audits), so a def survives across runs of the synthetic generator. The
generator's `_refresh_render_code` (PR #290) rewrites the reused def's
`render_code` from the _currently running_ template — so **regenerating
after a deploy is how new render code reaches an existing def**. There is
no separate "push template" step in the golden path. (You CAN hot-push
with the `connect_labs` MCP `workflow_sync_from_template_file`, but DON'T
— see the gotcha below.)

### Gotchas that cost real time

- **`workflow_sync_from_template_file` is destructive to a live def.** It
  rewrites `config` and `pipeline_sources`, which strips
  `config.templateType` and the pipeline link. A def that loses its
  `templateType` becomes invisible to the synthetic generator's reuse
  filter (`template_type == "<key>"`), so the next generation creates a
  _brand-new_ def → unbounded accumulation. We deleted **11 orphan CHC
  defs** on opp 10000 that this produced. Don't hot-push to a def you
  intend to keep regenerating against; deploy + regenerate instead.
- **Deploy-cutover lag** — see step 3. This bit us three times before the
  freshness guard existed.
- **Auto-flags need no reload (PR #294)** — chc merges `ensureAutoFlags`'s
  created flags into local state via `flagsForRow`, so pills appear on a
  fresh run's first mount. Before this, every "working" recording had been
  silently primed by an earlier load; a truly fresh run showed no flags
  until reload.
- **Defer the video past pre-warm (PR #294)** — the drill-through pre-warms
  ~20s on a warm page; `RecorderSession(defer_record=True)` +
  `start_recording()` keeps that out of the clip so it doesn't open on a
  blank screen.
- **Don't gate the audit scene on all photos decoding (PR #296)** — photos
  cold-fetch from GDrive; an upfront "wait for all 5" gate stalls the full
  timeout if one is slow. Confirm only the first photo, then
  `pass_each_audit_image` waits per-photo as it scrolls to each.
- **Menus flip up near the viewport bottom (PR #295)** — the flagged FLW is
  the last table row; its action dropdown opens above the trigger when
  there's no room below, so the options + the click are on-screen (and in
  the recording).
- **`image_results` is stale after audit completion** — a completed audit's
  persisted pass/fail/pending aggregate is NOT recomputed from the final
  per-photo results. The bulk page recomputes client-side so the UI is
  correct, but downstream reads (audit detail, PAR rollup) see stale
  counts. Latent backend bug, tracked separately; harmless for this demo
  (the manager-flow audit lives on an in_progress run no rollup reads).
