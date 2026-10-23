# Page and workflow sandbox: where it stands and how to finish it

**Status (2026-10-23): switched on by default.** `LABS_PAGE_SANDBOX` defaults to `true` and
`LABS_WORKFLOW_SANDBOX` to `all`, in `config/settings/base.py` and pinned in
`deploy/task-definitions/web.json`. Every page and every workflow draws in the frame;
`LABS_WORKFLOW_SANDBOX=off` / `opt_in` and `LABS_PAGE_SANDBOX=false` remain only as switches
back. A final audit before the switch (2026-10-23) found nothing that made the frame worse
than the unsandboxed render, and these gaps in what it promises, all fixed in the same PR:

- **Coaching** (`/tasks/<id>/ai/initiate/`): the frame named the recipient, bot and prompt and
  the question named only the task. The server now takes a sandboxed request only for the
  task's own worker (`tasks/views.py`), and the question names the bot and quotes the prompt.
- **Mapbox**: `*.tiles.mapbox.com` serves the whole Mapbox API (uploads, datasets) and allowed
  writes from `Origin: null`; it is named by `/v4/` now, and `events.mapbox.com` by its path.
- **Scope by another name**: `owning_program_id` (query and state bodies) and
  `storage_opportunity_id` are bound like `program_id` / `opportunity_id`;
  `owning_program_id` may also be the one in the page's own address (the KMC drill links).
- **Embed sessions**: `EmbedSessionMiddleware` refuses `Origin: null` on every method, as
  `sandbox_guard.py` does for the labs session.
- **Audit jobs**: a job's `job_config.opportunities`, `flw_opportunity_ids` and
  `only_opportunity_id` are bound (and the audit create/preview bodies' per-worker map); the
  question names the job type.
- **reconcile-generation** reads only the opportunities the frame's workflow covers.
- **Load**: workflow jobs 6 a minute, pipeline-running reads 6 at once, answers read with an
  early cut-off (`readCapped`); labs asks at most 20 questions a minute, and none for 5 s
  after a no.
- **Context**: the shell and `/static/` never read, save or redirect a labs context.
- The unused `CodeEditor.js` / `vibes.html` (a same-origin `srcDoc` frame) were deleted.

Left as is: a download is saved from labs' origin with the frame's bytes (CSV formulas, a
PDF), which is what a download is; a by-id audit save/delete names only the audit id (the
server checks the viewer's access); a removed embed client stays in the shell's
`frame-ancestors` for up to ~6 minutes (cache), with no data in the shell.

The section below is the history of how it got here.

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

| PR                                | What                                                                                                                                                                                                                                                                 |
| --------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| #2449, #2451                      | Phase 1: pages in the frame; review fixes                                                                                                                                                                                                                            |
| #2466, #2470, #2473, #2479, #2480 | Phase 2: workflows in the frame, route table, cross-workflow reads bound, page actions                                                                                                                                                                               |
| #2475, #2481                      | `workflow_sandbox_scan`; run-labs-command prints a whole log                                                                                                                                                                                                         |
| #2482                             | Compile-time rewrites (location, confirm), bridged EventSource, routes for live copies                                                                                                                                                                               |
| #2492                             | Every live workflow's needs supported; exemptions removed (migration 0007 drops the table)                                                                                                                                                                           |
| #2493                             | Canopy panel on every sharing page and workflow, sandboxed or not; end-to-end test `connect_labs/mcp/tests/test_canopy_panel_end_to_end.py`                                                                                                                          |
| #2489                             | Switched off on labs; `?sandbox=1` previews                                                                                                                                                                                                                          |
| #2496                             | The frame's CSP allows the marker-cluster plugin (used only by MBW v1 copies; see "MBW v1" below)                                                                                                                                                                    |
| #2644                             | localStorage / sessionStorage kept by the host per definition, across loads (the design doc's "Storage")                                                                                                                                                             |
| #2495                             | Pages and workflows size the same way: the frame grows to its content, the labs page scrolls (host viewport for `vh`/`h-screen`, fixed dialogs placed in view, scrolls go to the host). Matched plain vs `?sandbox=1` on 11 templates, 3 browsers, desktop and phone |

## Verified (2026-10-22)

- **Live scan** (`workflow_sandbox_scan --as-user jonathan`, run on labs): 658 definitions and
  45 seed templates, **0 breaks**; after #2644 the 17 `storage` findings are `info`. Coverage is
  the scopes Jonathan can read (`--all-users` has not been run: the owner's call).
- **Probes, Chromium + Firefox + WebKit, a workflow AND a page, sandbox on** (local server, a
  labs-only opportunity, Playwright): token mint (shimmed fetch and raw XHR), an unlisted labs
  API (relative and absolute), `document.cookie`, `parent.document`, `top.location` read and
  write, `window.open`, off-site fetch and image: **every one blocked**; labs' own
  `localStorage` key unseen in the frame; local and session storage kept across a reload, each
  definition under its own key. The harness is a probe render code, a seeder and a Playwright
  driver; rebuild it from this list if it is gone (it lived in a session scratchpad).
- Unit: 312 vitest sandbox tests, 156 Python sandbox tests, CI green.

## What only the owner can do

1. **Look, side by side, plain vs `?sandbox=1`**, signed in to labs:
   - Stock review `https://labs.connect.dimagi.com/supply/views/stock-review/?program_id=10112`
   - Forecast `https://labs.connect.dimagi.com/supply/views/forecast/?program_id=10112`
   - KMC worker review `https://labs.connect.dimagi.com/labs/workflow/5618/run/?run_id=5620&opportunity_id=10042&flw=10019%3A%3Aflw_001&source_run=5801`
     (also one with a real saved report)
   - Program home (page) `https://labs.connect.dimagi.com/labs/p/programme/10112/`
   - Org home (page) `https://labs.connect.dimagi.com/labs/p/org/labs-synthetic-labs-synthetic/`
   - 10730 (attach-pipeline flow asks first), 14556 (control room run links),
     `interviews_reporting_v2`, a Ward Progress Tracker (change a filter, reload: it stays)
   - The canopy panel on a page and a workflow: "Ask an agent" answers about what is on screen.
   - One touch scroll on a phone.
2. **Rule on the three residual differences from #2495:** a document-level `sticky top-0`
   sticks just below the labs header (plain, it slides under it); a dialog's backdrop does not
   cover the labs header and side nav, and the dialog sits ~32px lower; near the top of a page
   a tall dialog scrolls inside itself. *The dialog ones are fixed for a dialog the person
   opens (the frame is lifted over the window while it is open; page-sandbox spec, "Sizing").
   One opened with no click still draws below the header, and near the top of a page a tall
   one runs past the frame's top (its title and close button out of sight, not scrolling
   inside itself as said here).*
3. **Switch it on:** `LABS_PAGE_SANDBOX` to `true` and `LABS_WORKFLOW_SANDBOX` to `all` in
   `deploy/task-definitions/web.json` (PR, merge, deploy), then re-check the screens above.

## MBW v1

The deprecated `mbw_monitoring` package (Python job handler + stream; not the current workflow
architecture, which is `mbw_auditing_v5`/`v6`) has 11 live copies: 897 and 2642 on opportunity
765 hold real review sessions from March/April 2026; the rest are tests/QA copies on 740, 1167
and 709. No current template uses its endpoints. The sandbox supports them only because of the
no-exemptions ruling: the 5 `/custom_analysis/mbw_monitoring/...` routes (`sandbox_routes.py`,
`routes.ts`) and the marker-cluster CSP entry (#2496). Ali is deleting the copies (2026-10-22);
once they are gone, delete those routes, the CSP entry, the package, its job handler and its URL
entry (`DEPRECATED.md` lists them). Snapshot saving there is broken whether sandboxed or not.

## Cleanup

Leftover worktrees (`git worktree list`); the local `labs_sandbox_verify` database.

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
