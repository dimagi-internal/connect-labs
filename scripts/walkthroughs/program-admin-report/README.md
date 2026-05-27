# Program Admin Report — synthetic walkthrough scripts

This folder is the **single source of truth** for the cross-opportunity
Program Admin Report (PAR) demo: synthetic data config, the recorder
scripts that drive the live videos, and the HTML deck capture script.

Before this folder existed, the demo was reproducible only by copy-pasting
a large JSON payload into an MCP tool call from chat history and running
Playwright scripts that lived in `/tmp` — which made every iteration a
manual archaeology pass. Now the whole pipeline lives in one versioned
place.

## What it produces

1. A **synthetic data set** on labs prod (`labs.connect.dimagi.com`):
   2 opps, 4 weekly chc_nutrition runs each, real audits + tasks + decisions
   per FLW archetype, and a cross-opp Program Admin Report run that watches
   them. Northern's last week is left `in_progress` so the manager-flow
   video can show a real "do the review live" sequence.

2. A **manager-flow video** (`manager_flow.mp4`) — ~40s — covering the
   network manager arriving at the in_progress Wk4 review, bulk-marking
   the non-flagged FLWs as "No Issues", auditing the one flagged FLW
   (jumoke_n) live, navigating to the resulting task page, and firing the
   "Initiate AI Assistant" coaching chat with the pre-filled prompt.

3. A **drill-through video** (`drill_through.mp4`) — ~80s — the existing
   9-scene tour from the completed PAR grid down into one flagged FLW's
   audit pictures and OCS coaching task. (Records Northern Wk2 archetypes:
   hawa_n + bad MUAC.)

4. A **stakeholder HTML deck** (`program-admin-report.html`) — 12 slides
   wrapping the same scenes with persona narration + score annotations.

The intended final artifact for sharing is the concatenated MP4
(manager_flow + drill_through) plus the HTML deck.

## Pipeline

```text
demo_config.json
  │
  │   regenerate.py  ──────────────►  labs prod synthetic data
  │   (or MCP: program_admin_demo_seed_v2)
  │
  ▼
PAR_RUN_ID, WK4_RUN_ID, OPP_ID, WORKFLOW_DEF_ID
  │
  ├─► record_manager_flow.py  ──────►  /tmp/par_preview/video_manager/*.webm
  ├─► record_drill_through.py  ─────►  /tmp/par_preview/video/*.webm
  └─► capture_walkthrough.py  ──────►  /tmp/walkthrough-run-data.json
        │
        └─► canopy generate_presentation.py  ────►  program-admin-report.html
```

## Files

| File | What it does |
| ---- | ------------ |
| `demo_config.json` | The full opps + FLW archetypes + flag-week config passed to the synthetic generator. Edit this to change the demo narrative (add an FLW, move a flag, etc.). |
| `regenerate.py` | Loads `demo_config.json` and invokes `program_admin_demo_seed_v2`. Prints the run ids the recorders need. Requires `LABS_CONNECT_TOKEN` env var. |
| `record_manager_flow.py` | Playwright recorder for the 6-scene manager-flow video. Drives clicks live: PAR grid (Wk4 in_progress) → bulk Mark No Issue → Create Audit → audit pass page → Create Task with Coaching → Initiate AI Assistant modal → OCS conversation. |
| `record_drill_through.py` | Playwright recorder for the existing 9-scene drill-through video. Drives the completed PAR experience: grid overview → click Northern Wk2 → weekly review → drill into hawa_n's audit + task. |
| `capture_walkthrough.py` | Scene-by-scene screenshot capture used by canopy's `generate_presentation.py` to build the HTML deck. Reads the YAML spec at `docs/walkthroughs/program-admin-report.yaml`. |

## Running it end-to-end

```bash
# 1. Get the OAuth token for labs prod (one-time per session).
#    Extract from ~/.ace/labs-session.json or via ace:labs-login.
export LABS_CONNECT_TOKEN=...

# 2. Regenerate the synthetic data set + capture run ids.
eval $(python scripts/walkthroughs/program-admin-report/regenerate.py | tail -4)
# now PAR_RUN_ID, WK4_RUN_ID, OPP_ID, WORKFLOW_DEF_ID are exported

# 3. Record the manager-flow prepend (6 scenes).
python scripts/walkthroughs/program-admin-report/record_manager_flow.py

# 4. Record the drill-through (9 scenes).
python scripts/walkthroughs/program-admin-report/record_drill_through.py

# 5. Encode + concatenate to a single MP4.
#    (see drill_final.mp4 / manager_flow.mp4 outputs in /tmp/par_preview)
ffmpeg -i manager_flow.mp4 -i drill_through.mp4 \
  -filter_complex "[0:v][1:v]concat=n=2:v=1" -c:v libx264 -crf 23 program-admin-report.mp4

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
- Labs OAuth token in env or `~/.ace/labs-session.json`
- The labs MCP server connection in `~/.claude/mcp.json` if invoking via Claude
- Canopy plugin (`~/emdash-projects/canopy`) for the HTML deck generator

## Known cleanup — synthetic generator location

The synthetic data generator code lives at
`commcare_connect/mcp/tools/program_admin_demo_v2.py` because it's exposed
as an MCP-callable tool (the labs MCP `program_admin_demo_seed_v2`). The
file has grown to ~33KB of demo-specific seeder logic — it's no longer a
"thin MCP tool wrapper". A follow-up PR should:

- Move the demo seeder body to `commcare_connect/labs/synthetic/program_admin_demo_seeder.py`
  (next to `archetypes.py` and `manager_flow_views.py` — its real home).
- Leave a thin `commcare_connect/mcp/tools/program_admin_demo.py` that
  imports + `@register`s the seeder. Delete the legacy v1 file in the
  same location.
- Drop the `_v2` suffix from both the file name and the MCP tool name.

## Evolution notes

Things that were tricky and might bite again:

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
  real OCS account. The `task_initiate_ai` view detects the synthetic
  bot id and attaches a canned conversation instead of doing the real
  OCS round-trip.
- **PAR snapshot doesn't include in_progress runs**: the snapshot's
  `watched_summary` only captures completed runs. The Wk4 in_progress
  run must be discovered via env var (`WK4_RUN_ID`), not via the PAR
  snapshot API.
- **Audit + decision leftovers wedge the recorder**: if a previous
  recorder run created an audit + decision for the bad-MUAC FLW, the
  next run sees "View audit/task" instead of "Create Audit". Always
  regenerate (which cleanup_firsts) before re-recording.
