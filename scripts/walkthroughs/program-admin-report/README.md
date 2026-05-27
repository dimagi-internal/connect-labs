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
   2 opps, 4 weekly chc_nutrition runs each, real audits + tasks + decisions
   per FLW archetype, and a cross-opp Program Admin Report run that watches
   them. Northern's last week is left `in_progress` so the manager-flow
   video can show a real "do the review live" sequence.
2. A **manager-flow video** (`manager_flow.mp4`, ~40s) — the network
   manager arriving at the in_progress Wk4 review, bulk-marking the
   non-flagged FLWs as "No Issues", auditing the one flagged FLW
   (jumoke_n) live, navigating to the resulting task page, and firing
   the "Initiate AI Assistant" coaching chat with the pre-filled prompt.
3. A **drill-through video** (`drill_through.mp4`, ~80s) — the 9-scene
   tour from the completed PAR grid down into one flagged FLW's audit
   pictures and OCS coaching task.
4. A **stakeholder HTML deck** (`program-admin-report.html`) — 12 slides
   wrapping the same scenes with persona narration + score annotations.

The intended final artifact for sharing is the concatenated MP4
(manager_flow + drill_through) plus the HTML deck.

## Pipeline

```text
demo_config.json
   │
   │  regenerate.py  ──►  labs prod synthetic data
   │                       writes .run_ids.json + runs verify checks
   ▼
.run_ids.json
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

## Files

| File                      | What it does                                                                                                                                                                     |
| ------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `demo_config.json`        | The opps + FLW-archetypes + flag-weeks config passed to the synthetic generator. Edit this to change the demo narrative.                                                         |
| `regenerate.py`           | Loads `demo_config.json`, calls `program_admin_demo_seed`, writes `.run_ids.json`, and runs the verify checks. Requires `LABS_CONNECT_TOKEN`.                                    |
| `.run_ids.json`           | Generated. Holds `par_run_id`, `wk4_run_id`, `opp_id`, `workflow_def_id`. The recorders read this — never falls back to a stale hardcoded int.                                   |
| `record_manager_flow.py`  | Playwright recorder for the manager-flow video. Reads `.run_ids.json`; drives clicks via `_lib/recorder.py` primitives.                                                          |
| `record_drill_through.py` | Playwright recorder for the completed-PAR drill-through. Uses `_lib/discovery.py` to walk the PAR snapshot and pick "good" + "incomplete" drill targets.                         |
| `capture_walkthrough.py`  | Screenshot pass for the HTML deck. Each scene is keyed by a `target` keyword in `docs/walkthroughs/program-admin-report.yaml`; this script maps target → URL + post-load action. |

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
`apply_decision_spec`, …) live in
`commcare_connect/labs/synthetic/walkthrough_kit.py` — those are the
pieces a second walkthrough should import to avoid reinventing the
LabsRecord write plumbing.

## Running it end-to-end

```bash
# 1. Get the OAuth token for labs prod (one-time per session).
export LABS_CONNECT_TOKEN=...
# Optional: override the Playwright session file location.
# export LABS_SESSION_FILE=~/.ace/labs-session.json

# 2. Regenerate the synthetic data + write .run_ids.json + verify.
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
3. Write a thin `regenerate.py` that calls your seeder + uses
   `_lib.config.write_run_ids` to persist whatever ids your recorders
   need.
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
concat, FLW row scroll/click) are already in `_lib/`; the new
walkthrough should be ~60 LOC of scene sequence + a few hundred lines
of demo-specific seeder.

## Evolution notes (footguns to watch for)

- **In_progress runs need a snapshot fallback**: chc_nutrition's render
  code reads `instance.snapshot.pipelines` as a third fallback after
  `view.pipelines` and the top-level `pipelines` prop. Without this,
  in_progress synthetic runs show "No data available" because the live
  CSV pipeline returns nothing in the synthetic env.
- **Severity 2 for muac-flagged FLWs**: severity 1 produces SAM ~3.6%
  which doesn't trip the chc_nutrition Actions cell's `isFailing` gate
  (`SAM > 5%`). Severity 2 (~22% SAM) makes the flag visually obvious
  AND blocks the bulk Mark No Issue button on that row.
- **Clean FLWs no upward SAM-bin jitter**: ±1 jitter on the first two
  bins of severity-0 distributions can accidentally push to 7% SAM,
  tripping isFailing. Jitter clamped to `[-1, 0]` on those bins.
- **OCS bot list short-circuit**: synthetic opps return a single canned
  "MUAC Coaching (Synthetic Demo Bot)" so the existing "Initiate AI
  Assistant" modal renders with a selectable bot — without requiring a
  real OCS account.
- **PAR snapshot doesn't include in_progress runs**: the snapshot's
  `watched_summary` only captures completed runs. The Wk4 in_progress
  run id must come from `.run_ids.json`, written by `regenerate.py`.
- **Audit + decision leftovers wedge the recorder**: if a previous
  recorder run created an audit + decision for the bad-MUAC FLW, the
  next run sees "View audit/task" instead of "Create Audit". Always
  regenerate (which cleans first) before re-recording. The verify
  checks in `regenerate.py` catch most of these post-seed.
