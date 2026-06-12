# PAR walkthrough — actions map

Scene-by-scene record of every interaction the two Playwright recorders
(`record_manager_flow.py`, `record_drill_through.py`) perform, expressed in
canopy's action vocabulary (`kind: goto | click | click_menu | fill | select |
scroll_to | scroll | wait_for | hold`, with `css:` / `text:` target prefixes).
`${var}` placeholders reference the FLAT vars JSON the synthetic generator
(`regenerate.py`) emits at `.run_ids.json`.

**Purpose**: this is the source material for the future DDD unified spec
(`docs/walkthroughs/program-admin-report.yaml` gains real `actions:` blocks).
The recorders stay in the tree until a DDD render proves parity against this
map — do NOT delete them before that.

Selector provenance: `record_manager_flow.py`, `record_drill_through.py`,
`_lib/grid.py` (PAR grid cell clicker), `_lib/recorder.py` (menu/row/photo
primitives).

---

## Global gotchas (apply to every scene)

- **`networkidle` never fires on labs.** The PAR page background-polls its
  snapshot, audit pages stream GDrive JPGs, and task pages long-poll
  `/tasks/<id>/ai/sessions/`. Every wait must be a selector/text/function
  wait, never a network-idle wait.
- **Freshness preflight moved to generation time.** The synthetic generator
  aborts when labs serves stale template code (ECS worker-cutover lag), so the
  spec can assume the UI matches the local checkout. (The recorders still run
  `assert_page_current` as a belt-and-braces check after their first goto.)
- **Always regenerate before rendering.** Leftover audits/tasks flip the
  flagged row's menus from "Create Audit"/"Create Task" to "View Audit"/"View
  Task" (state-aware, PR #289) and the manager-flow scenes then can't create
  anything live.
- **Menus flip up near the viewport bottom (PR #295).** The flagged FLW is the
  last table row; its action dropdown opens _above_ the trigger. Menu items
  live in `css:div.absolute.z-20 button` (no stable test id — match on visible
  button text, exact match preferred, `includes` fallback).
- **Per-photo wait pattern (PR #296).** Audit photos cold-fetch from GDrive.
  Never gate on "all 5 photos decoded" up front — confirm only the FIRST photo
  (`css:img[src*="/audit/image/"]`, `complete && naturalWidth > 0`), then wait
  per-photo (cap ~8s each) as you scroll to it.
- **Recording deferral / pre-warm.** The drill-through recorder visits every
  target URL once on a non-recorded page first (warms the GDrive image cache,
  ~20s) and only then starts the video (`defer_record=True`). A DDD render
  should either keep a warm pass or budget longer photo waits.
- **Scenes that END on an audit page need a must-succeed first-image wait +
  settle hold.** A soft (log-and-continue) image wait let a scene's final
  frame land mid-load with five blank photo tiles (DDD iter1, scenes 2 + 12).
  Make `css:img[src*="/audit/image/"]` a `must_succeed: true` wait (~30s),
  then hold ~1.5-2s before the scene's closing holds / any Pass clicks.
- **Caret-to-end in a textarea: `ControlOrMeta+End` does NOT move the caret
  on darwin.** It scrolled the page and left the caret mid-word, so the
  append typed "househol Please be friendly.ds" (DDD iter1, scene 5). Use
  `press: ControlOrMeta+ArrowDown` (end of textarea content on macOS),
  optionally followed by `press: End`, before `type`.
- **Cursor parking — park via `hover` on neutral chrome, never rely on where
  the last click left the cursor.** Two failure shapes from iter1: (a) the
  inline detail panel inserts BELOW the clicked row and shifts later rows
  up/down under a parked cursor — it visually landed on the OTHER cluster's
  week cell while the first cluster's cell wore the SELECTED badge; (b) the
  scene-start cursor occluded the breadcrumb / the "WINDOW AGGREGATE" header.
  Known-good neutral park targets on the PAR page: `text:opportunities
watched` (top-strip summary line), `text:Run detail` (detail-panel header
  chrome), and the aggregate cards' runs-count lines (`text:4/4 runs` /
  `text:3/4 runs` — beside, not on, the SOP MET / BELOW pills).
- **Week-window contract (post-restructure).** The PAR window is the trailing
  4 COMPLETED weeks (Northern 4/4 → aggregate "SOP MET"; Southern 3/4 with a
  missed week + the open-work week → "BELOW"). The manager-flow in-progress
  run is the CURRENT week, OUTSIDE the PAR window, so the grid never shows a
  "NO RUN" hole for the week the manager is filmed working. `regenerate.py`
  computes all Mondays from today's date — the demo stays current-dated. The
  emitted var keys `wk4_run_id` / `wk4_url` are kept for spec stability; they
  point at the current-week in-progress run (the name is historical).

---

## Manager flow (`record_manager_flow.py`, ~40s)

Target page: the in_progress CURRENT-week weekly review,
`${wk4_url}` = `/labs/workflow/${workflow_def_id}/run/?run_id=${wk4_run_id}&opportunity_id=${opp_id}`.
(The `wk4` var name is historical — the run is the live current week, outside
the PAR window.) The flagged FLW is `${flagged_flw_manager}`
(archetype-derived; `jumoke_n` in the shipped config).

### M0 — Arrive at the current-week in_progress review

```yaml
- kind: goto
  target: ${wk4_url} # 60s timeout
- kind: wait_for
  target: text:${flagged_flw_manager}
- kind: hold
  seconds: 2.5 # React hydration settle
```

### M1 — Auto-flags appear on mount

The framework calls `view.ensureAutoFlags` on mount, POSTs computed flags to
`/flags/`, and the table re-renders with one pill per flag after the
post-write refetch. On a freshly generated run the round-trip takes 15–25s.

```yaml
# 45s timeout — NOT the default. Match ANY of the canonical flag labels
# (PR #285): "SAM rate < 1%" | "MAM rate < 3%" | "Gender split outside 40-60%"
- kind: wait_for
  target: text:SAM rate < 1%
- kind: hold
  seconds: 1.5
```

(The recorder uses a regex over `document.body.innerText`:
`/SAM rate < 1%|MAM rate < 3%|Gender split outside 40-60%/`.)

### M2 — Create Audit → "Audit Last 7 days"

The Actions cell is a split MenuButton (PR #286 catalog): trigger
`Create Audit` opens `{New Audit, Audit Last 7 days}`.

```yaml
- kind: scroll_to
  target: css:tr:has-text("${flagged_flw_manager}") # block: center
- kind: hold
  seconds: 1.2
- kind: click # row-scoped: button inside that <tr> only
  target: css:tr:has-text("${flagged_flw_manager}") button:has-text("Create Audit")
- kind: hold
  seconds: 1.8 # menu-open dwell so the options are readable
- kind: click_menu # items render in css:div.absolute.z-20 button;
  target: text:Audit Last 7 days # glide cursor + ~0.9s dwell before the click
- kind: wait_for
  target: url:**/audit/** # 30s
- kind: wait_for
  target: text:Total Assessments # 30s — the audit header, NOT networkidle
- kind: wait_for # ≥1 decoded /audit/image/ thumbnail, 20s cap
  target: css:img[src*="/audit/image/"]
- kind: hold
  seconds: 1.2
```

Failure modes the recorder guards: trigger missing → a previous run already
created the audit (re-run the synthetic generator, `cleanup_first: true`);
menu item missing → menu didn't open / stale template.

### M3 — Pass each photo, then Complete Image Review

One `css:.assessment-widget` per photo (5). For each, in DOM order:

```yaml
# repeat per .assessment-widget (index 0..4):
# scroll block: center — also triggers the widget's lazy image load
- kind: scroll_to
  target: css:.assessment-widget:nth-of-type(${n})
# THIS widget's img decoded; cap 8s, then move on
- kind: wait_for
  target: css:.assessment-widget:nth-of-type(${n}) img
# skip if the widget already shows the green already-passed badge (.bg-green-600)
- kind: click
  target: css:.assessment-widget:nth-of-type(${n}) button >> text:Pass
- kind: hold
  seconds: 0.6 # per-photo dwell
```

Then:

```yaml
- kind: click # EXACT text match; button reads "Save Progress"
  target: text:Complete Image Review # until all 5 photos are reviewed
- kind: wait_for # commit signal — the page redirects to the workflow list
  # once the completion POST lands. Wait for the URL change, NOT a fixed
  # hold: a hold ends the scene on a mid-save frame ("Saving..." spinner /
  # "Unsaved changes" badge), i.e. an uncommitted audit (iter3 judge).
  must_succeed: true
  seconds: 20
  target: url:**/labs/workflow/**
- kind: hold
  seconds: 1.5
```

(`click_text_exact` deliberately skips any networkidle wait — GDrive image
streaming keeps the network busy forever. The bulk page's green "Completed
on …" banner only renders server-side on a later visit, so the redirect IS
the only reliable on-page committed-state signal.)

### M4 — Back to the Wk4 review

```yaml
- kind: goto
  target: ${wk4_url}
- kind: wait_for
  target: text:${flagged_flw_manager}
- kind: hold
  seconds: 2.5
```

### M5 — Create Task → "Coach on Flag implications"

The coaching item only renders when the row carries any flag (PR #286) — true
for `${flagged_flw_manager}` once auto-flags applied in M1. The trigger label
is `Create Task` (renamed from "Send Task" in PR #285). The onClick composes
`{description, coaching_prompt}` from the row's actual `flag_label` values
(PR #282).

```yaml
- kind: scroll_to
  target: css:tr:has-text("${flagged_flw_manager}")
- kind: hold
  seconds: 1.2
- kind: click
  target: css:tr:has-text("${flagged_flw_manager}") button:has-text("Create Task")
- kind: hold
  seconds: 1.8 # menu-open dwell (menu flips UP — last row)
- kind: click_menu
  target: text:Coach on Flag implications
- kind: wait_for
  target: url:**/tasks/** # 30s
- kind: wait_for
  target: text:Initiate AI Assistant # 30s — task page long-polls, no networkidle
- kind: hold
  seconds: 2.5
```

### M6 — Open the "Initiate AI Assistant" modal

The bot dropdown populates from `/tasks/api/ocs/bots/`, which short-circuits
to the canned synthetic bot for labs-only opps. The prompt textarea pre-fills
from `task.data.coaching_prompt` (PR #282).

```yaml
- kind: click
  target: css:button:has-text("Initiate AI Assistant")
- kind: wait_for # textarea prefilled: value.length > 50; 15s
  target: css:textarea[placeholder="Instructions for the bot..."]
- kind: wait_for # synthetic bot option present; wait on the SELECT via :has() —
  # bare `option` targets are invisible inside a closed select and burn the
  # whole timeout as frozen film (hit in DDD iter1/iter2: 20s dead frame)
  target: css:select:has(option[value="synthetic-muac-coaching"])
- kind: select # native <select> — use select, never click; MUST scope to the
  # bot select via :has() — the task page has other <select>s (Status) and a
  # bare css:select silently selects nothing (hit in DDD iter0)
  target: css:select:has(option[value="synthetic-muac-coaching"])
  value: synthetic-muac-coaching # must dispatch input + change events
- kind: hold
  seconds: 1.5
```

### M7 — Edit the prompt slightly

Conveys "the manager is tailoring this". Focus the textarea, move the caret to
the end, then type (60ms/char in the recorder). **Caret gotcha:**
`ControlOrMeta+End` does not move the caret in a textarea on darwin — the
append lands mid-word. Use `ControlOrMeta+ArrowDown` (+ `End`).

```yaml
- kind: click # focus the textarea
  target: css:textarea[placeholder="Instructions for the bot..."]
- kind: press # caret to end of content (macOS-safe; NOT ControlOrMeta+End)
  value: ControlOrMeta+ArrowDown
- kind: press # then end-of-line
  value: End
- kind: type # APPEND, don't replace
  value: ' Please be friendly.'
- kind: hold
  seconds: 1.5
```

### M8 — Initiate AI → coaching conversation appears

The modal's confirm button reads exactly `Initiate AI` — NOT the outer
`Initiate AI Assistant` button. Exact-match the text. The synthetic
short-circuit writes the conversation onto `task.data.ocs_conversation`
and the modal reloads the page ~2s after success. At initiate time only
the manager's instruction banner + the assistant's OPENING message exist,
both stamped now() (≥ the task's own creation — a fuller backdated
transcript used to predate the task and read as canned; mid-conversation
and closed states belong to the SEEDED tasks).

```yaml
- kind: click
  target: text:Initiate AI # exact match, enabled buttons only
- kind: wait_for
  target: text:Coaching Conversation # 30s; survives the page reload
- kind: hold
  seconds: 2
- kind: scroll_to
  target: text:Coaching Conversation # block: center
- kind: hold
  seconds: 3
```

---

## Drill-through (`record_drill_through.py`, ~80s)

Target page: the completed cross-opp PAR run,
`${par_url}` = `/labs/workflow/${par_def_id}/run/?run_id=${par_run_id}&opportunity_id=${opp_id}`.
Drill targets are resolved by the synthetic generator at generation time
(formerly the recorder's own snapshot walk): the "good" drill is
`${flagged_flw_good}`'s closed-satisfactory audit+task pair, the "incomplete"
drill is `${flagged_flw_incomplete}`'s in-review audit + investigating task.

Pre-warm (not recorded): visit `${par_url}`, `${audit_good_url}`,
`${audit_incomplete_url}`, `${task_good_url}`, `${task_incomplete_url}` once
each so the GDrive image cache is hot; on audit pages wait for ≥1 image.

### D1 — PAR grid overview

```yaml
- kind: goto
  target: ${par_url} # 60s timeout
- kind: wait_for
  target: text:Window aggregate # the PAR page's content marker
- kind: hold
  seconds: 3 # cursor drifts to ~(50, 60), 1s + 2s dwell
```

### D2 — Click the "good run" grid cell → inline detail panel

**The PAR grid is `<div>`-based, not a `<table>`** — there is no row/cell
selector. The canonical locate-and-click (from `_lib/grid.py`):

1. Find the label div: `div` with inline `style.fontWeight === '600'` whose
   `textContent.startsWith("${good_opp_label}")` (prefix match — "Northern"
   finds "Northern Cluster").
2. `labelCell = label.closest('div[style*="border"]')`; the grid row is
   `labelCell.parentElement`; week cells are `row.children[1 + week_idx]`
   (child 0 is the label).
3. Click the inner `[style*="cursor: pointer"]` element (fall back to the
   cell), via mouse coordinates at the cell's center (so a synthetic cursor
   overlay can animate; `locator.click()` skips the overlay's mousemove).

```yaml
- kind: click # see locate steps above
  target: css:par-grid-cell(${good_opp_label}, ${good_week_idx}) # pseudo-target
- kind: hold
  seconds: 3 # detail panel slides out under the row
```

**Cursor parking after the cell click**: the panel inserts below the clicked
row and shifts every later row — a cursor parked at fixed viewport coords ends
up visually resting on the OTHER cluster's week cell. After the panel renders,
re-park with `hover: text:Run detail` (the panel's header chrome).

### D2b — "Open the run" → CHC Nutrition weekly review

```yaml
- kind: click
  target: text:Open the run
- kind: wait_for
  target: text:FLW-Level Analysis
- kind: wait_for # tbody tr count >= 5; 12s
  target: css:tbody tr
- kind: scroll_to # the flagged row is the one whose text
  target: css:tr:has-text("View Audit") # includes the state-aware button
- kind: hold
  seconds: 2.5 # cursor parks on the View Audit button
```

State-aware flip (PR #289): because the audit already exists, the Actions cell
reads `View Audit` / `View Task` (title-case) instead of the create menus.
Pre-#289 builds rendered lowercase "View audit" — don't match on that.

### D3 — Audit page (good run): the reviewed photos

```yaml
- kind: click
  target: text:View Audit
- kind: wait_for # tolerant, 10s — assessment count header
  target: text:Showing 5 assessment(s)
- kind: wait_for # ≥3 decoded /audit/image/ thumbnails; 15s
  target: css:img[src*="/audit/image/"]
- kind: hold
  seconds: 4
```

(Direct-URL alternative: `goto: ${audit_good_url}`.)

### D4 — Back to the table, then the task + coaching transcript

```yaml
- kind: goto # the recorder uses history back
  target: back
- kind: wait_for # tbody tr >= 5; 8s
  target: css:tbody tr
- kind: hold
  seconds: 0.6
- kind: click
  target: text:View Task
- kind: wait_for
  target: text:Closed # 8s, tolerant
- kind: hold
  seconds: 2.5
- kind: scroll_to # heading is an <h3>
  target: text:Coaching Conversation
- kind: hold
  seconds: 7.5 # read the transcript
- kind: scroll
  value: '350' # smooth scrollBy to reveal more transcript
- kind: hold
  seconds: 3.5
```

Fallback if `View Task` is absent: go back to `${par_url}`, re-click the good
cell, then `click: text:Task #${good_task_id}` in the detail panel (post-wait
`text:Closed`). (Direct-URL alternative: `goto: ${task_good_url}`.)

### D5 — Back to PAR, the "incomplete" cell

```yaml
- kind: goto
  target: ${par_url}
- kind: wait_for
  target: text:Window aggregate
- kind: hold
  seconds: 0.6
- kind: click
  target: css:par-grid-cell(${incomplete_opp_label}, ${incomplete_week_idx})
- kind: hold
  seconds: 2.5
```

### D6 — The in-review audit (work in progress)

The detail panel links audits by id — the click target is literal text.

```yaml
- kind: click
  target: text:Audit #${incomplete_audit_id}
- kind: wait_for
  target: text:Save Progress # in-review marker (vs "Complete Image Review")
- kind: wait_for # only ≥2 decoded thumbnails; 12s — some photos
  target: css:img[src*="/audit/image/"] # are still pending placeholder cards
- kind: hold
  seconds: 4
```

### D7 — The investigating task (coaching mid-conversation)

```yaml
- kind: goto
  target: ${par_url}
- kind: wait_for
  target: text:Window aggregate
- kind: hold
  seconds: 0.6
- kind: click
  target: css:par-grid-cell(${incomplete_opp_label}, ${incomplete_week_idx})
- kind: hold
  seconds: 0.6
- kind: click
  target: text:Task #${incomplete_task_id}
- kind: wait_for
  target: text:Close Task # the still-open task's action button
- kind: hold
  seconds: 2.5
- kind: scroll_to
  target: text:Coaching Conversation
- kind: hold
  seconds: 6
```

### D8 — Back to the aggregate, linger

The closing beat must NOT be a pixel-identical replay of the grid-overview
scene — give it its own motion: scroll to the aggregate column, then hover
each cluster's aggregate card in turn (the SOP MET vs BELOW contrast IS the
scene). Hover the runs-count line inside each card, beside — not on — the
verdict pill, and never park on the "WINDOW AGGREGATE" column header.

```yaml
- kind: goto
  target: ${par_url}
- kind: wait_for
  target: text:Window aggregate
- kind: scroll_to
  target: text:Window aggregate
- kind: hover # Northern's aggregate card — the SOP MET side
  target: text:4/4 runs
  seconds: 2
- kind: hold
  seconds: 1.5
- kind: hover # Southern's aggregate card — the BELOW side; final rest
  target: text:3/4 runs
  seconds: 2
- kind: hold
  seconds: 3
```

---

## capture_walkthrough.py target → URL map (deck screenshots)

The HTML-deck capture keys each YAML scene by a `target` keyword. With the
vars JSON, every target is now a direct interpolation:

| target                  | URL / action                                                                            |
| ----------------------- | --------------------------------------------------------------------------------------- |
| `par_grid`              | `goto ${par_url}`                                                                       |
| `par_detail_good`       | `goto ${par_url}` + grid-cell click `(${good_opp_label}, ${good_week_idx})`             |
| `par_detail_incomplete` | `goto ${par_url}` + grid-cell click `(${incomplete_opp_label}, ${incomplete_week_idx})` |
| `par_aggregate`         | `goto ${par_url}`                                                                       |
| `chc_good`              | `goto ${chc_good_url}`                                                                  |
| `audit_good`            | `goto ${audit_good_url}` + wait ≥3 audit images                                         |
| `audit_incomplete`      | `goto ${audit_incomplete_url}` + wait ≥2 audit images                                   |
| `task_good`             | `goto ${task_good_url}` + scroll_to `text:Coaching Conversation`                        |
| `task_incomplete`       | `goto ${task_incomplete_url}` + scroll_to `text:Coaching Conversation`                  |
