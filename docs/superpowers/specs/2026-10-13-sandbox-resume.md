# Page and workflow sandbox: where it stands and how to finish it

**Status (2026-10-13, paused for a demo):** the sandbox is **switched off on labs** (PR #2489:
`LABS_WORKFLOW_SANDBOX=off`, `LABS_PAGE_SANDBOX=false` in `deploy/task-definitions/web.json`).
Pages and workflows draw as they did before it. Adding `?sandbox=1` to any address
(`&sandbox=1` if it already has a `?`) previews the sandboxed render to whoever asks.
Design and every ruling: [`docs/superpowers/specs/2026-10-12-page-sandbox-design.md`](2026-10-12-page-sandbox-design.md).

## The owner's rulings that set the finish line

- **Pages are workflows with no runs.** A page may do everything a workflow does except what
  needs a run. No page-only restrictions.
- **No exemptions.** Workflows must be able to do everything they do today, and more over time,
  inside the sandbox: through host-checked bridge calls, asked first where a write reaches beyond
  the workflow's own run. (The exemption mechanism from #2484 was removed in #2492.)
- **It must look the same.** An embedded-looking frame is a non-starter. The frame is borderless
  inside the normal labs chrome; the remaining visible difference, a workflow scrolling inside its
  frame, is what #2495 removes. Pages and workflows size the same way: the frame grows to its
  content and the labs page scrolls.
- **100% correct, canopy SDK included,** before it is switched back on, and **the owner sees it
  first** (side-by-side plain vs `?sandbox=1`), then decides.
- **No sandbox status notes in the UI** for Dimagi staff (#2486 closed, #2488).

## What is merged

| PR | What |
|---|---|
| #2449, #2451 | Phase 1: pages in the frame; review fixes |
| #2466, #2470, #2473, #2479, #2480 | Phase 2: workflows in the frame, route table, cross-workflow reads bound, page actions |
| #2475, #2481 | `workflow_sandbox_scan`; run-labs-command prints a whole log |
| #2482 | Compile-time rewrites (location, confirm), bridged EventSource, routes for live copies |
| #2492 | Every live workflow's needs supported; exemptions removed (migration 0007 drops the table) |
| #2493 | Canopy panel on every sharing page and workflow, sandboxed or not; end-to-end test `connect_labs/mcp/tests/test_canopy_panel_end_to_end.py` |
| #2489 | Switched off on labs; `?sandbox=1` previews |
| #2495 | Pages and workflows size the same way: the frame grows to its content, the labs page scrolls (host viewport for `vh`/`h-screen`, fixed dialogs placed in view, scrolls go to the host). Matched plain vs `?sandbox=1` on 11 templates, 3 browsers, desktop and phone |

## What is open

- **Residual differences from #2495**, to show the owner and decide on: a document-level
  `sticky top-0` sticks just below the labs header (plain, it slides under the header); a dialog's
  dark backdrop does not cover the labs header and side nav, and the dialog centres in the window
  below the header (~32px lower); near the top of a page a tall dialog scrolls inside itself.
  Untested: touch scrolling on a real phone; `kmc_flw_review` with a real saved report (only its
  empty state was compared locally).
- **#2496 `sandbox-markercluster`** (draft): the frame's CSP allows the marker-cluster plugin the
  MBW GPS map uses. CI not yet checked.

## Steps to finish, in order

1. **Land #2496.** Check its CI (`gh pr checks 2496`), fix anything red, mark ready, squash-merge.
2. **Run the checks that were not run in a real browser:**
   - The probes (token mint, labs API fetch, cookie, `parent.document`, `top.location`,
     localStorage, `window.open`, off-site fetch/image) on a sandboxed page AND workflow, in
     Chromium, Firefox and WebKit: every one blocked.
   - The canopy panel in a browser on a page and a workflow, sandboxed and not: the widget loads,
     the share/selection reaches the agent, the panel token is `page:read` on a page and
     `workflow:read`+`workflow:act` on a run.
   - `interviews_reporting_v2` (never finished loading locally in either mode) and an MBW v1
     copy's stream (needs a CommCare login).
   A local harness was written by the agent in its session scratchpad (`sandbox-verify-harness/`);
   if that is gone, rebuild from the probes listed in the spec. Use a local runserver
   (`make manage CMD="runserver"`), the Postgres container `connect-labs-db-1`, and the browse
   binary `~/.claude/skills/gstack/browse/dist/browse` (not on PATH).
3. **Deploy main** (owner-approved deploy; check migrations since the last deploy, at least
   `workflow/0007`, so `run_migrations=true`):
   `gh workflow run deploy-labs.yml --repo dimagi-internal/connect-labs --ref main --field run_migrations=true`
4. **Live checks with the sandbox still off**, each plain and with `?sandbox=1`:
   - Scan: `gh workflow run run-labs-command.yml --repo dimagi-internal/connect-labs --ref main --field command="workflow_sandbox_scan --as-user jonathan --summary"`.
     Expect **0 breaks** under `all` (19839 calls a removed endpoint and reports as info).
   - Screens to compare side by side and send the owner:
     - Stock review `https://labs.connect.dimagi.com/supply/views/stock-review/?program_id=10112`
     - Forecast `https://labs.connect.dimagi.com/supply/views/forecast/?program_id=10112`
     - KMC worker review `https://labs.connect.dimagi.com/labs/workflow/5618/run/?run_id=5620&opportunity_id=10042&flw=10019%3A%3Aflw_001&source_run=5801`
     - Program home (page) `https://labs.connect.dimagi.com/labs/p/programme/10112/`
     - Org home (page) `https://labs.connect.dimagi.com/labs/p/org/labs-synthetic-labs-synthetic/`
     - 10730 (attach-pipeline flow asks first) and 14556 (control room run links)
   - The live canopy checklist (panel on a sharing page and a workflow; "Ask an agent" answers
     about what is on screen).
5. **Show the owner**, then on the owner's word switch it on: set `LABS_PAGE_SANDBOX` to `true`
   and `LABS_WORKFLOW_SANDBOX` to `all` in `deploy/task-definitions/web.json` (PR, merge,
   deploy), and re-check the screens above live.
6. **Cleanup:** remove leftover worktrees (`git worktree list`), and the local test records for
   opportunity 10790.

## Known items outside the sandbox

- **KMC worker review case figures are slow** (13-29 s): `GET /labs/workflow/api/5456/semantic/?scopes=case&flw=...`
  computes all of a worker's cases live (249 x 122 columns) though the page shows one. Not the
  sandbox. Owned by another thread.
- **14556's opportunities:** it now answers only for opportunities in both its `opportunity_ids`
  and its `config.opp_meta`; add the missing ones with `workflow_update_opportunity_ids`.
- **MBW snapshot saving** fails on the server whether sandboxed or not (`save_run_snapshot` no
  longer exists); its sandbox route is bound for when it is fixed.
- **Scan coverage:** real-opportunity workflows are scanned only in scopes the named user can read.
  Scanning everyone's (`--all-users`) reads through each person's stored Connect login; that is the
  owner's decision and has not been made.
